# 运行时骨架（S8-A/B 的地基）

> 这份文档只讲**框架**：一个 run 怎么走、停在哪、怎么恢复、副作用怎么不重复。
> 业务判断（S6）、界面（S8-C）都还没接，代码里用 `STUB` 标着。

## 1. 一句话

把现在散落的函数调用串成 **一条有状态的流水线**：

```
normalize → coverage → retrieve → judge → [gate_judgement] → compose → verify
        → [gate_action] → act → finalize
```

方括号里的两步是**可能停住**的地方——停住不是异常，是状态。

## 2. 五个文件，五个概念

| 文件 | 概念 | 一句话 |
|---|---|---|
| `app/runtime/state.py` | `RunState` | 一次 run 的全部状态，纯 JSON 可存 |
| `app/runtime/events.py` | 事件流 | **只追加**，审计真源 |
| `app/runtime/store.py` | 存储 | `data/runs/<id>/{state.json,events.jsonl}` |
| `app/runtime/gates.py` | 门控 | `raise PendingGate` = 停下来等人 |
| `app/runtime/actions.py` | 副作用 | 按**条目**幂等，重复调用不新增 |

引擎只有 60 行（`app/runtime/engine.py`），因为它只做三件事：记事件、调函数、存状态。

## 3. 三条不变量（有测试守着）

1. **每个 step 有开始/结束两条事件** —— 崩溃后能看出死在哪一步。
   `tests/test_runtime.py::test_events_are_append_only_with_sequence`
2. **恢复只重入被中断的 step** —— 之前的步骤一次都不再跑。
   `test_resume_does_not_repeat_finished_steps`
3. **副作用按条目幂等** —— 批准两次只写一次。
   `test_satisfied_gates_action_then_approve_writes_once`

另有一条端到端的：`test_resume_from_a_fresh_process` —— 新建一个 `Engine`（等于新进程）
从磁盘上的 `state.json` 恢复，事件流与原来的完全一致。

## 4. 状态长什么样

```json
{
  "run_id": "run_aa601a2289c3",
  "question": "硕士毕业落户能加多少分",
  "step": "gate_action",
  "status": "waiting",
  "city": "上海", "year": null,
  "hits": [{"doc_no": "沪教委学〔2026〕13号", "citation": "…（第 21 页 · PDF 原件）", "score": 5.67}],
  "judgement": {"verdict": "satisfied", "total_known": 80, "total_upper": 114, "standard": 72},
  "answer": {"text": "满足。已知得分 80（标准 72）。", "verified": [{"verbatim": true}]},
  "action": {"kind": "todo", "items": [{...}, {...}, {...}]},
  "gate": {"kind": "action", "question": "将写入 3 条待办，需要批准"}
}
```

- `status ∈ {running, waiting, done, failed}`
- `gate` 非空 ⇔ `status == waiting`
- `done_steps` 用来防"重复执行"，也是审计页的时间线原料

## 5. 怎么跑

```bash
# 一次 run（会在第一个门控停住）
.venv/bin/python -m tools.run "硕士毕业落户能加多少分" --profile profile.json

# 看状态 / 事件
.venv/bin/python -m tools.run --list
.venv/bin/python -m tools.run --show run_xxx --events

# 提交决定，继续
.venv/bin/python -m tools.run --resume run_xxx --action approve --by sheep

# HTTP（同一套状态）
.venv/bin/python -m uvicorn app.api.server:app --port 8000
curl -s localhost:8000/api/runs -H 'content-type: application/json' \
  -d '{"question":"硕士毕业落户能加多少分","profile":{...}}'
```

## 6. 门控的语义

| 门槛 | 什么时候停 | 可用的决定 |
|---|---|---|
| `judgement` | 判定为 `unknown`（缺材料 / 没接入判定器） | `confirm_unknown` / `reject` |
| `action` | 判定为 satisfied/direct，且要写待办 | `approve` / `deny` |
| `clarify` | **尚未实现**（等画像补全逻辑） | — |

关键：**停住的是状态，不是异常**。进程可以死，重来之后从同一个 step 重入，
把决定喂进去继续。这与 LangGraph 的 `interrupt()` / `Command(resume=...)` 语义一致：
节点会从头跑，所以 interrupt 之前不能有写操作——`act` 之前只有纯计算。

## 7. 副作用为什么按"条"幂等

一次批准写 3 条待办。如果幂等键是"整批"，那么"批到第 2 条时崩了"就无法区分
"写了 1 条"和"一条没写"。

所以每条待办单独算键：`sha256(run_id | kind | item)`。重放时逐条比对，
已存在的跳过——**批准两次、崩溃三次，最终仍然是 3 条**。

## 8. 现在是 STUB 的地方（诚实清单）

| 位置 | 现在 | 以后 |
|---|---|---|
| `judge()` | 只有落户有判定器，其余一律 `unknown` | S6 扩面（社保年限 / 补贴资格） |
| `gate_judgement` | 只处理 unknown | 冲突检测（两个证据矛盾） |
| `clarify` | 不存在 | 画像缺字段时停下来要材料 |
| `compose()` | 模板字符串，没有生成模型 | 接本地/云端模型，只解释判定 |
| `verify()` | 只做"逐字包含" | 三层引用（可定位 / 能支撑 / 能适用） |
| `actions.apply` | 写本地 JSON | 换 MCP 工具，只替换 `_write` |
| 存储 | 文件（单机可读） | Postgres（只换 `Store` 三个方法） |
| 界面 | `/` 一页占位 | S8-C：判定卡 + 证据链 + 审计时间线 |

## 9. 为什么不直接上 LangGraph

- 现在的骨架 **60 行**，每一步做什么一眼能看完；LangGraph 版本要理解 `StateGraph`、
  reducer、checkpoint 序列化，才能回答"恢复时哪个节点会重跑"。
- 面试里被问"你的恢复粒度是什么"，答"super-step 边界，被 interrupt 的节点整体重跑"，
  需要真的踩过这个坑——手写版本踩过一次（`gate_action` 重入时 `act` 被调两次，
  靠幂等键挡住），比看文档记得牢。
- 迁移成本可控：`Engine.advance` 的三件事（记事件、调函数、存状态）分别对应
  LangGraph 的 callbacks、node、checkpointer。

## 10. 下一步

1. **S8-B 补齐**：`clarify` 门槛 + 审批的 TTL / 单次消费 / 参数绑定
2. **S8-C 界面**：判定卡 → 证据链（点开原文跳页码）→ 审批 → 审计时间线
3. **副作用线换真 MCP**：`mock 办理计划` 服务 + `actions.py` 的 `_write` 替换
4. **judge 扩面**：第二个判定器，证明四态不是为落户定制的
