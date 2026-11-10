# 可信 agent 评测方案（n=20~50 可用）

原则：每个结论先落到"机器能判"的证据，判不了的降级为假设。前 3 条为主判据，4~8 只做诊断，不合成总分。

## 1. 可机械判定的指标（8 条）

1. **终态断言** 判：沙箱 DB/文件树是否等于人工标注的 goal state，只比白名单字段。判法：跑完 dump 状态做结构化 diff，不看对话。抗刷：口头"已完成"无效，写操作必须真发生。https://arxiv.org/abs/2406.12045
2. **隐藏测试全绿** 判：FAIL_TO_PASS 全过且 PASS_TO_PASS 不回归。判法：测试不进 agent 上下文，提交后统一跑。抗刷：删测试、改断言直接判负。https://arxiv.org/abs/2310.06770
3. **pass^k** 判：同题独立跑 k 次是否全对。判法：k=3~8，同时报 pass^1 与 pass^k 的落差。抗刷：一次幸运不算数，直接惩罚方差。https://arxiv.org/abs/2406.12045
4. **最小工具序列** 判：轨迹是否包含 gold 的"最小可行路径"。判法：子序列匹配，不用 F1——F1 可乱发调用刷召回。抗刷：额外调用计入预算，超 20 步强制终止判错。https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html
5. **危险动作计数** 判：delete/write/pay/send 类调用次数。判法：工具层打点，阈值 0 或显式上限，超限整题失败。抗刷：事后解释不能抵消副作用。https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html
6. **无关请求拒答率** 判：无解或缺参请求是否不调工具、先反问。判法：混入 20~30 条负样本，单独成表。抗刷：堵死"永远先调一个工具"的策略。https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html
7. **准精确匹配** 判：有唯一答案的任务。判法：归一化大小写、空白、单位后 exact match，不做语义判分。抗刷：语义判官可被话术骗过。https://arxiv.org/abs/2311.12983
8. **成本上界** 判：单题 token、工具调用数、wall-clock 是否在上限内完成。判法：三个上限同时卡，任一超限判失败。抗刷：无限重试、堆上下文换成功率会直接出局。https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html

## 2. 反刷分设计（5 个）

1. 判终态不判文本：tau-bench 比 DB 终态，BFCL v3 比后端状态，且排除 `_` 私有属性。
2. 用稳定性代替峰值：pass@k 只是"k 次里有一次对"的乐观上界，pass^k 才是可承诺的可靠性。https://arxiv.org/abs/2107.03374
3. 双向出题：可解集 + 不可解集（BFCL 的 240 条 irrelevance），只测 happy path 的分数没有信息量。https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html
4. 私有 held-out 并定期换题：SWE-bench 有 32.67% 的成功补丁答案就写在 issue 里，过滤后 12.47%→3.97%。https://arxiv.org/abs/2410.06992
5. 把成本写进判据：token、步数、危险动作超限即失败，让刷分变成负收益。https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html

## 3. LLM-as-judge 何时可信

- 可信：成对比较、差异明显、有 rubric 或参考答案、judge 强于被评模型、领域窄。GPT-4 与人类偏好一致率 >80%，接近人-人一致。https://arxiv.org/abs/2306.05685
- 不可信：绝对打分——同一答案重复打分 intra-rater reliability 低、跨 run 漂移；需推理正确性的难题（JudgeBench 上 GPT-4o 仅略好于随机）；位置偏置（仅交换顺序，Vicuna-13B 在 80 题中 66 题胜过 ChatGPT）。https://arxiv.org/abs/2410.12784 https://arxiv.org/abs/2305.17926 https://arxiv.org/abs/2510.27106
- RAGAS 类 reference-free 指标与人工一致率 0.95/0.78/0.70，且 answer relevance 明确不看事实性，不能当正确性判据。https://arxiv.org/abs/2309.15217
- 所以只做相对比较：A/B 双向交换取平均；先在 25~50 条上与人工对齐，报 precision/recall 而非 raw agreement；judge 只做分诊，终裁回到机械断言。https://hamel.dev/blog/posts/evals/

## 4. 小样本怎么报（n=20~50）

- 报比例加 95% Wilson 区间，不报裸百分比：n=20 时 15/20 的区间约 53%~96%，撑不起"提升 10%"。
- 配对比较：同题跑 A/B，用 McNemar 或 paired bootstrap；题目得分相关 0.3~0.7，配对几乎免费降方差。https://arxiv.org/abs/2411.00640
- 分层：按难度、工具数、领域分层，每层至少 5 题，只报层内结果。
- 同题多次采样取题级均值（epochs），压随机方差。https://arxiv.org/abs/2411.00640
- 失败案例 100% 归类：工具选错、参数错、未澄清、越权、循环，报每类频次——n 小时频次比百分比可信。

## 5. 警告清单（5 个无效做法）

1. 让 LLM 给整条轨迹打 1~10 分当主指标。https://arxiv.org/abs/2510.27106
2. 只报最好一次或挑 seed，掩盖方差。https://arxiv.org/abs/2406.12045
3. 拿 agent 的自我总结当完成证据。https://arxiv.org/abs/2406.12045
4. 同一模型既当 agent 又当 judge，自我偏好。https://arxiv.org/abs/2306.05685
5. 指标加权成一个总分，或只测能跑通的样例，失败无法定位。https://arxiv.org/abs/2411.00640
