# 明确虚构的输入

由 `generate_fake_data.py` 生成，编号从 session_90 开始。

- `valid/`：与真实 data/ 同构，只有相对时间和数值，以及仅有起止时间的虚构标注/预测。
- `adversarial/`：故意不合格的文件名、表头、文字值和数值错误；不是可分析研究输入。
- ZZTEST_Qx7 和 7777.7777 是输出泄漏测试标记，不代表任何参与者或真实测量。

仅 `python run_real.py --fake` 读取 valid/；自动测试使用 adversarial/ 检验拒绝与脱敏错误输出。不要把真实资料放入这里。
