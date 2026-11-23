# 崩溃恢复：从"能续上"到"副作用只发生一次"

这个项目的主线之一是**可恢复**。可恢复不是"重启后还能打开这个 run"，而是：

> 进程在最坏的时刻被 `kill -9`，重启之后，**该做的事都做了、不该多的事一件没多**。

`docs/FOUNDATION.md` §8 把这一条写成了验收：拔电、幂等、单写者、对账、观测。
这里是它的实测记录。

## 两个中断点

| 点 | 位置 | 库里留下什么 | 恢复动作 |
|---|---|---|---|
| A | 两个 step 之间（等人批准） | `status=waiting`，`gate` 完整 | `resume(action=approve)` |
| B | 一个 step 内部、副作用已写之后 | `status=running`，`step=act`，无 gate | `resume(action=recover)` |

A 是常见情况；B 是**真正难的那个**——系统不知道自己做到哪，而外面的世界已经变了。

## 语义（为什么能说"可观察的恰好一次"）

1. **恢复只重入被中断的 step**。`RunState.step` 在进入下一步之前就落盘，
   所以崩溃后指针停在中断点上，之前的 step 不会重跑。
2. **重入是 at-least-once**。被中断的 step 会再执行一次——这是事实，不粉饰。
3. **`act` 按条目幂等**。`actions` 表以 `(run_id, kind, item_key)` 为主键，
   重复写入变成 `reused`，所以 at-least-once × 幂等 = **可观察的 exactly-once**。
4. **完成的 run 拒绝再推**（409）。这是最后一道闸：宁可报错，不重复执行。

## 实测

完整输出见 `docs/RECOVERY-DEMO.md`。两个关键片段：

```
# A：批准前 kill -9，重启后继续
status=waiting step=gate_action gate=action events=17
status=done applied=3 reused=0
HTTP 409  {"detail":"run_... 已完成，不能重复执行"}
```

```
# B：act 已写完待办、step_finished 未落盘时硬退出
status=running step=act gate=None
status=done applied=0 reused=3 keys=3
这个 run 的待办条数 = 3
  #21 act step_started
  #22 act run_recovered        ← 恢复留下的痕迹
  #23 act step_started
  #24 act step_finished
```

B 里 `applied=0 / reused=3` 是整个设计的落点：**重跑了一遍，一条没多**。

## 怎么复现

```bash
bash ops/demo_recovery.sh          # 两个中断点一起演，约 40 秒
python -m tools.demo.crash_mid_act # 只造 B 那个"卡住的 run"
```

单测（不需要服务）：

```bash
pytest tests/test_recovery.py -q
```

`tests/test_recovery.py` 两条分别覆盖 A/B：A 用 `KeyboardInterrupt` 打断第一个 step，
B 把状态改回 `running/step=act` 再恢复，断言 `applied=0, reused=3, actions.count==3`。

## 边界（必须说清楚）

- **外部系统可能已经执行**。我们重入 `act` 时，调用的是本地待办表；如果换成真的
  对外 API，幂等要靠对方的幂等键，或者两阶段（先写意图、再对账）。
  `docs/FOUNDATION.md` 的结论是"承诺可观察的恰好一次，不承诺物理恰好一次"。
- **失败不自动重试**。`step_failed` 会落盘并把 run 标成 failed，需要人看——
  自动重试会把"确定性错误"变成"重试风暴"。
- **恢复粒度是 step**，不是指令。一个 step 内部如果有多处写操作，只能靠该 step
  自己的幂等键，引擎不提供事务。
