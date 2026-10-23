# 离线窗口分析与人工标注对比

这套工具直接使用 Tom 已经计算出的能量曲线，增加 2 秒、4 秒平均、粗分段和事件级 Hit/Miss 对比。它不重新计算手表信号，也不修改 Tom 的算法。

真实文件由使用者本人在终端运行。开发和测试只使用虚构数据；不要向聊天粘贴真实文件、曲线截图、编号对照表或实际运行参数。

## 先用假数据试运行

在本仓库目录下，使用 Python 3.10 或以上版本：

```powershell
python -m unittest discover -s tests -v
python -m sync_eval demo
```

核心计算、提取和 CSV/JSON 输出只用 Python 标准库。可选图表沿用项目原有的 Plotly 依赖：

```powershell
python -m sync_eval demo --output-dir local-results/synthetic-with-plot --plot
```

假数据故意含有虚构姓名、日期和备注，以测试这些信息不会出现在能量副本中。结果在 `local-results/synthetic-demo/`；这里所有数值和标签均为虚构。

固定演示采用试验阈值 3 和 6。2 秒与 4 秒两种窗口的预期结果都是：2 个 Hit、1 个 Miss、1 个未匹配预测。演示的人工作为完整标注，因此这个未匹配预测也可计为 1 个误报。这些数字只用于验证程序，不是研究结果。

## 文件命名与本地目录

| 位置 | 内容 | Git 处理 |
| --- | --- | --- |
| `local-data/session_01_energy.csv` | 两列：`time_s,energy` | 忽略 |
| `local-data/session_01_annotations.csv` | 三列人工事件表 | 忽略 |
| `private/session_map.csv` | 编号、原始文件路径、时间起点及偏移的对应关系 | 忽略，单独本地保存 |
| `local-results/session_01/` | 2 秒、4 秒窗口、分段、预测和对比结果 | 忽略 |
| `templates/session_01_annotations.csv` | 只有表头的空模板 | 可以提交 |
| `examples/synthetic/session_01_annotations.csv` | 明确虚构的示例 | 可以提交 |

后续编号使用 `session_02`、`session_03` 等，不使用真实编号、姓名或日期。程序拒绝覆盖已存在的同名结果或重复映射编号。

`.gitignore` 也忽略 `data/`、Parquet、普通 CSV、HTML、JSON 和临时文件，只允许提交明确的模板和假数据 CSV。Git 忽略规则不会自动移除上游已经跟踪的样例数据；本次不向仓库加入任何真实输入或分析结果。

原始文件保持不变。编号对照表仍含敏感关联信息，不能与脱敏副本一起上传。仅保留两列数值能减少直接身份信息，但不代表文件已被正式认证为完全匿名；副本仍按项目研究数据管理。

## 时间如何与录像对齐

**当前只有能量曲线、没有录像或对齐资料，也可以先做窗口平均和粗分段。** 提取时用 `--time-origin first`，程序在本机自动取曲线第一个点作为 0 秒，不需要填写真实日期时间：

```powershell
python -m sync_eval prepare --kind plotly --input "LOCAL_REPORT.html" --session session_01 --trace-name "mean cross energy" --time-format datetime --time-origin first --energy-scale linear
python -m sync_eval analyze --input local-data/session_01_energy.csv --session session_01 --plot
```

输出图和汇总会明确标为“曲线起点后的秒数，尚未对齐录像”。`session_01_energy.timebase.json` 只记录使用哪种时间参照，不包含日期；真实曲线起点仍只保存在私有对照表中。人工标注对比会拒绝把这种时间直接当作录像时间。

之后拿到录像起点或标注对齐资料时，再显式设置时间起点。只有进行录像/人工标注对比时，双方才需要统一使用“录像开始后的秒数”。转换公式：

```text
录像秒数 = 原始时间 - time_origin + offset_seconds
```

- `--time-origin first`：只以曲线首点为 0 秒，不声称已对齐录像。
- `--time-origin` 的明确时间值：录像第 0 秒对应的原始时间。只有确认对齐信息后才填写，程序不把曲线首点冒充录像开始。
- `--offset-seconds`：再加一个已确认的偏移量，默认 0。正数向录像较晚时刻移动，负数向较早时刻移动。
- `--time-format seconds`：输入是数值秒数或 `MM:SS` / `HH:MM:SS`。
- `--time-format datetime`：输入是 ISO 日期时间；起点也须采用对应格式，时区需一致。
- `--window-origin`：窗口网格在当前时间轴上的起点，默认 0。因此两秒窗口为 `[0,2)`、`[2,4)`；四秒窗口为 `[0,4)`、`[4,8)`。

例如：能量点的原始时间为 110 秒，录像起点对应原始 100 秒，偏移量为 +2 秒，那么此点位于录像第 12 秒，而不是第 0 秒。

时间起点和偏移必须用录像/项目记录确认。本工具保留 Tom 输出的时间信息供显式转换，不会自动修复 STFT 帧时间或视频时钟误差。所有含真实日期的对齐参数只写入本地对照表。

`analyze` 默认从同名 `.timebase.json` 自动识别时间参照；没有参照文件时标为 `unspecified`。旧的已确认录像时间文件可显式用 `--time-reference video`，但不要用这个参数假装已经完成对齐。

## 本人提取真实曲线时使用

**以下是供使用者本人执行的命令模板；开发助手不会运行它们。** 路径、日期和参数均为占位示例。应先确认项目允许的运行环境及处理范围。

已有 CSV 的情况：

```powershell
python -m sync_eval prepare --kind csv --input "LOCAL_ENERGY_FILE.csv" --session session_01 --time-column timestamp --energy-column energy --time-format seconds --time-origin 100 --offset-seconds 0 --energy-scale linear
```

从 Tom 已有的 Plotly HTML 报告提取，以下日期为虚构占位值：

```powershell
python -m sync_eval prepare --kind plotly --input "LOCAL_REPORT.html" --session session_01 --trace-name "mean cross energy" --time-format datetime --time-origin "2099-01-01T10:00:00" --offset-seconds 0 --energy-scale linear
```

HTML 只作为文本/JSON 解析，程序不会执行原报告的 JavaScript、打开浏览器或连接服务。必须恰好找到一条指定的能量曲线；多个同名曲线或不支持的格式会停止处理。

输出只包含 `time_s,energy`。其余列、报告标题、备注、悬浮信息和原始文件名不复制到此文件。来源路径与对齐设置只记录在 `private/session_map.csv`。

默认丢弃位于录像第 0 秒之前的点；如项目需要保留录像前的数据，可显式添加 `--keep-pre-video`。顺序混乱、重复时间、无穷值和非数值能量会使校验失败；缺失能量保持缺失。

必须声明能量标度。Tom 演示中的 `mean_cross_energy(..., db=False)` 使用 `--energy-scale linear`；若确实是 dB 输入，使用 `--energy-scale db`，先转换为线性标度再平均。工具不改变或恢复 Tom 上游已经做过的缩放、裁剪或插值。

## 2 秒和 4 秒粗分段

本人在本地准备好文件后：

```powershell
python -m sync_eval analyze --input local-data/session_01_energy.csv --session session_01 --plot
```

- 非重叠窗口，左闭右开；每个窗口取有效能量点的算术平均，不插值。
- 默认从输入曲线有效点的第 1/3、2/3 分位数选择两个探索阈值。**同一组阈值用于 2 秒和 4 秒结果**，避免两种窗口各自切出固定比例。
- 低于第一个阈值为 `low`；大于等于第二个阈值为 `high`；其余为 `medium`。
- 可用 `--thresholds LOW HIGH` 指定线性能量阈值，或 `--quantiles 0.25 0.75` 比较另一方案。阈值相等时程序拒绝硬分三类。
- 连续相同类别合并成区间；默认只有 `high` 作为预测同步候选。可用 `--positive-levels medium high` 合并中、高候选。
- `high` 只是高能量候选，不等于已经确认的临床同步。
- 使用中位采样间隔估计每窗应有的点数，可通过 `--sample-period` 覆盖。有效点比例不足 `--min-coverage`（默认 0.8）的窗，以及首尾不完整窗口，标为 `unknown`，不生成同步预测。
- 程序只能识别能量输入中仍存在的缺失。上游已经插值/补零的区间无法由该曲线自动恢复。

输出包括 `session_01_2s_windows.csv`、`session_01_4s_windows.csv`、两种窗口的 `_segments.csv`、`_predictions.csv`，以及 `session_01_summary.json`。可选图表为 `session_01_comparison.html`，内嵌绘图库，不使用在线 CDN。

## 人工标注表

复制 `templates/session_01_annotations.csv` 到 `local-data/`，仅填写已确认的同步事件：

```csv
start,end,activity_type
8,16,instrument_playing
18,20,singing
40,48,body_movement
```

以上行均为假数据。`start/end` 使用录像秒数，也可填 `MM:SS` 或 `HH:MM:SS`。区间为 `[start,end)`，开始必须小于结束。

活动类型只接受以下代码，避免自由文字带入姓名或临床细节：

| 代码 | 活动 |
| --- | --- |
| `instrument_playing` | 演奏乐器 |
| `singing` | 歌唱 |
| `body_movement` | 身体动作 |
| `narrative_play` | 叙事性游戏 |
| `other` | 其他，详细解释另存受控记录 |

该表中的每行是一个**人工确认的同步事件**；普通活动区间不能自动当作同步事件。未标注时间默认不代表“没有同步”。

## Hit/Miss 对比规则

```powershell
python -m sync_eval analyze --input local-data/session_01_energy.csv --session session_01 --annotations local-data/session_01_annotations.csv --output-dir local-results/with-annotations --min-iou 0.5
```

这是可配置的初步事件级协议，并非 Anna 已批准的最终评分标准：

1. IoU = 两个区间重叠的时长 / 两个区间并集的时长，默认至少 0.5 才可匹配；仅边界相接不算重叠。
2. 一对一最大匹配：一个预测最多命中一个人工事件，一个人工事件最多被命中一次。使用增广路径避免贪心选择少算命中。
3. 匹配成功的人工事件为 Hit；未匹配的人工事件为 Miss。结果提供按活动类型统计及匹配索引，索引对应输入行顺序。
4. 不在有效分析覆盖区间内，或跨越缺失窗口的人工事件排除，不算 Miss。与被排除事件重叠的预测也排除，避免错误计为误报。
5. 没有人工对应的预测记为 `unmatched_predictions`，待复核。只有本人确认标注覆盖完整并加上 `--annotations-complete`，才计算误报和 precision。
6. 无参考事件或无预测时，不强行把未定义比例写成零；不会输出未经负例定义支持的 accuracy/specificity。

控制台只输出成功或安全的错误代码，不打印原始行、数值、路径、异常正文或异常堆栈。错误代码仅含阶段（PREPARE/ANALYZE）、固定类别（例如 FILE_NOT_FOUND、ALREADY_EXISTS、VALUE_ERROR）与本项目公开源码中的函数/行号；可以只反馈这一行帮助排错，不需要回传真实文件或截图。
