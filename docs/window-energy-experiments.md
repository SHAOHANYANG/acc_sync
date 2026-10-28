# Window-Based Energy Analysis：Energy + Duration

当前任务依据：用户于 2026-10-27 提供的 10/23 会议任务总结。该日期的原始会议台词尚未归档；不要把本工具的参数写成 Anna 已确定的标准。用户再次确认：助手只编程和使用假数据；真实研究文件由用户本人在独立终端处理。

## 已有流程与本次复用

- 保留 `acc_sync/` 与 `sync_eval analyze` 的原有行为。
- 输入为已提取的两列 CSV：`time_s,energy`。时间为秒，能量为线性指标，不在 dB 值上直接取平均。
- `time_s=0` 当前是曲线首点，不自动等于录像起点。旁边的 `.timebase.json` 保存 `curve/video/unspecified`。
- 参考脚本的原始手表采样频率为 25 Hz；STFT 分析窗为 5.12 s；配置 hop=0.5 s 转为整数样本后为 12/25=0.48 s。25 Hz 不是能量曲线的采样频率，实际曲线间隔由输入时间差中位数记录。
- 复用 `window_average`：2 s / 4 s 不重叠的 `[start,end)` 算术均值，默认网格起点为 0。恰好在右边界的点放入下一窗。
- 有效点不足预计数量的 80%，或边缘不满一个完整窗口，标为 unknown。保留缺失，不插值，不删除异常高值，不跨 unknown 合并。
- `--reuse-windows` 可指定先前的 session 结果目录。程序检查时间参照、网格、覆盖率、窗口边界与数值；一致后使用原导出均值。检查仍调用同一套均值函数，不进行 STFT，不修改原文件。

时间对齐待核对项：源码同时使用 `center=True` 和 `frames_to_time(..., n_fft=n_fft)`。后者按库说明会添加半个 FFT 窗的偏移，原本用于非居中的 STFT（[librosa 源码说明](https://librosa.org/doc/0.11.0/_modules/librosa/core/convert.html#frames_to_time)）。因此从源码推断可能多加约 2.56 秒，需要 Tom 确认；本轮不更改其代码，也未用真实数据验证偏移。常量偏移在曲线首点归零时会抵消，但不能据此声称已对齐录像。Task A 的相对时间探索可以继续，人工标注比较前必须确认时钟。

## 两种规则

### A. 单阈值 + 最短持续时间

1. 选出均值 `E >= T` 且有效的窗口。
2. 合并时间相接的合格窗口。
3. 合并后的 `end-start >= D` 才保留为候选事件。

持续时间条件在合并之后判断；一个 2 秒窗口不能先被 D=8 筛掉，再去寻找连续四窗。短暂低于阈值会切断事件，当前不跨低谷或缺失合并。

### B. 高能量较短 OR 较低能量较长

```text
(E >= T_high 连续至少 D_short)
OR
(E >= T_low  连续至少 D_long)
```

其中 `T_low < T_high`，`D_short < D_long`。两条分支先独立合并及判断持续时间，再对接受的事件取并集。重叠或相接事件合并一次，避免重复计数；保留 high_short / lower_long / 两者的来源字段。

较低阈值能够保留更弱、更持久的能量变化，也可能保留持续噪声；这不是已经验证的同步定义。两条分支的 OR 是同时存在的两套条件，不是只用低阈值把所有高能量事件扩大。

## 默认小规模参数网格

| 参数 | 默认值 |
|---|---|
| 输出平均窗 | 2 s、4 s |
| 候选能量阈值 | 原曲线有效能量的 50%、66.7%、80%、90% 分位数 |
| 单规则最短时间 | 2、4、8、12 s |
| 双规则高能量最短时间 | 2、4 s |
| 双规则较低能量最短时间 | 8、12 s |
| 跨间隙合并 | 0 s，不跨间隙 |

4 个阈值各配 4 种持续时间，形成 16 个单规则设置；6 对高低阈值各配 4 种长短时间，形成 24 个双规则设置。共 40 种设置 × 两种窗宽 = 80 次运行。分位数对应值重复时，部分双规则会跳过，汇总会记录重复值数量。

为了直接比较窗宽，默认从同一条输入曲线一次确定阈值，两种窗宽共享相同数值。参数网格用于敏感性探索，不通过候选事件数量选择“最优”。若希望分别调节两种窗宽，应另设校准数据和统一评估集，不能在同一数据上调阈值再宣称验证成功。

`--thresholds 2 3 4 6` 可以改用人工指定数值。这里的数字只是运行参数示例；Energy≈4 不是已确定阈值，真实数据须先确认指标的尺度和单位。

持续时间被窗口网格量化：4 秒窗下，请求 D=2 和 D=4 的最小可接受片段都为 4 秒。CSV 同时记录 requested 和 effective duration；相同结果不是两次独立证据。相邻能量帧本身还受 5.12 秒上游分析窗影响，2 秒平均不意味着 2 秒检测精度。

## Synchrony / Non-Synchrony 比较

新的空模板是 `templates/session_01_reference_labels.csv`：

```csv
start,end,label
```

label 只允许：`synchrony`、`non_synchrony`、`uncertain`。必须来自独立人工编码，不能从能量高低或程序候选片段生成。正例事件表中没有写到的时间，不能自动补成 non_synchrony。

调用需要同时声明：

- `--annotation-source independent_human`
- `--labels-independent`
- `--alignment-confirmed`
- `--annotation-time-reference curve` 或 `video`，且与能量时间参照一致。

声明不足时，程序在打开标注文件前停止。上述开关只是研究者确认，不会自动验证标签来源或完成时钟对齐。

一个有效窗口必须被同一人工类别完整覆盖才进入该组。跨类别边界、uncertain、缺少标注及无效能量窗口均排除并记数。不能因此删除原始记录。

输出两组样本数、均值、中位数、标准差、四分位数，以及描述性的 point-biserial r。该 r 是二元标签与连续能量的相关，等价于对应的 Pearson r（[SciPy 官方说明](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.pointbiserialr.html)）。不会把相关系数叫准确率，也不计算将相邻窗口当成独立样本的 p 值。若需推断，应另行设计按会话/参与者或时间块处理依赖性的分析。

没有独立标注时，Task B 明确写为 not_run。Task A 仍可运行，不会生成假标签。现有 start,end,activity_type 正事件模板继续用于后续事件 Hit/Miss，不能直接替代本任务所需的独立正/负区间。

## 输出

每次输出到新的 `output-dir/session_XX/`，拒绝覆盖已有同名 session 结果。所有文件使用编号，不写原始来源路径或临床文字。

| 后缀 | 内容 |
|---|---|
| `_rule_grid.csv` | 每次运行的阈值、时长、候选数量和有效时间占比 |
| `_candidate_events.csv` | 每个候选事件的 start/end/duration、窗口数、能量和分支来源 |
| `_width_comparison.csv` | 同一规则下 2/4 秒的候选数量、时长、共同有效时间内的区间 Jaccard |
| `_windows.csv` | 两种窗宽的均值、缺失质量与边界信息 |
| `_experiment.json` | 全部参数、实际能量点间隔、来源校验值和比较状态 |
| `_report.md` | Methods / Results / Observations / Limitations / Questions |
| `_exploration.html` | 本地交互图：曲线、固定示例候选区间、参数热图、可选标签组箱线图 |
| `_labelled_windows.csv` | 仅有独立标注时：窗口类别与排除原因 |
| `_energy_groups.csv` | 仅有独立标注时：两组能量描述统计 |

区间 Jaccard 仅衡量两种窗口结果的时间一致程度，限制在共同有效时间内；没有任何候选时记为空，不伪造“100%一致”。两种窗口一致不代表同步判断正确。

HTML 内嵌绘图库，无 CDN、外部字体或统计上传。控制台只显示固定 SUCCESS / 安全错误码；真实数值留在用户本地结果文件中。两列数值、编号和 Git 忽略不能自动证明数据已经完全匿名或获得外部 AI 使用许可。

## 假数据演示

```powershell
python -m sync_eval explore-demo --output-dir local-results/synthetic-window-energy-demo --plot
```

这是人为构造的能量序列和先定义的虚构参考区间，不是用原始手表数据模拟 Tom 的完整 STFT，也不是临床研究结果。随机种子固定为 20261027，能量点间隔 0.48 s；包括较强短变化、较弱长变化、非同步突发及缺失片段。演示使用 2、3、4、6 四个虚构能量阈值，测试 80 次规则运行。

## 真实数据：只由使用者本人执行

助手不会执行本节命令，也不读取结果。先在批准的本地环境打开新的 PowerShell。

第一步，进入用户自己的 fork：

```powershell
Set-Location -LiteralPath "C:\Users\Shaohan Yang\Documents\Codex\2026-10-15\zhao-d\outputs\NYU-VIP-Musical-Connections\code-and-analysis\acc_sync"
```

第二步，指定已经安装依赖的 Python：

```powershell
$syncPython = "C:\Users\Shaohan Yang\Documents\Codex\2026-10-14\zhe\work\acc_sync\.venv\Scripts\python.exe"
```

第三步，复用此前 session_02 的数值副本和窗口均值；每次只粘贴完整的一行：

```powershell
& $syncPython -m sync_eval explore --input ".\local-data\session_02_energy.csv" --session session_02 --reuse-windows ".\local-results\curve-first\session_02" --output-dir ".\local-results\energy-duration-20261028" --plot
```

这个命令没有提供人工标签，所以只运行 Task A。它不会重做 Tom 的信号处理，也不会覆盖旧均值、旧图或原始数据。若指定输出目录已存在同名 session，停止；重跑时把输出目录改为另一个新名称，例如 energy-duration-20261028-run2。

确认 SUCCESS 后，第四步，在本机打开新图：

```powershell
Invoke-Item -LiteralPath ".\local-results\energy-duration-20261028\session_02\session_02_exploration.html"
```

第五步，在本机打开实验摘要或结果文件夹：

```powershell
Invoke-Item -LiteralPath ".\local-results\energy-duration-20261028\session_02"
```

不要把真实 CSV、曲线、标签或实验数值发送给助手。只反馈 SUCCESS / FAILED 安全提示即可。

本节个人路径仅为本机运行指引，不含研究输入内容；对照表、临床数据与新导出仍由 .gitignore 排除。代码可进入个人 fork，上游保持只读。
