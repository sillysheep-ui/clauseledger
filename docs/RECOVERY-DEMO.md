# 崩溃恢复演示（原始输出，2026-11-23）

```
$ bash ops/demo_recovery.sh
 
 === A1. 启动服务 ===
 uvicorn pid=3275
 
 === A2. 提一个需要人批准的问题（停在 gate_action） ===
 run_id=run_79b83b976c63 status=waiting step=gate_action gate=action
 
 === A3. kill -9（不给收尾机会） ===
 端口已关闭
 
 === A4. 重启（新进程，同一个 SQLite） ===
 新 pid=3326
 
 === A5. run 还在，停在同一步 ===
 status=waiting step=gate_action gate=action events=17
 
 === A6. 用原 run_id 继续 ===
 status=done applied=3 reused=0
 
 === A7. 同一个 run 再批一次（409，不能重复写） ===
 HTTP 409  {"detail":"run_79b83b976c63 已完成，不能重复执行（需要重跑请新建 run）"}
 
 === B1. 最坏中断点：act 写完待办、step_finished 之前硬退出 ===
 run_id=run_c8af5c82ee48 待办已写入，现在 os._exit(9)
 stuck run=run_c8af5c82ee48
 
 === B2. 重启后它的状态 ===
 status=running step=act gate=None
 
 === B3. 恢复（重入 act，副作用按条幂等） ===
 status=done applied=0 reused=3 keys=3
 
 === B4. 这个 run 的待办条数（应当仍是 3） ===
 3
 
 === A8/B5. 事件链（run_recovered 是恢复留下的痕迹） ===
   #19 gate_action step_started
   #20 gate_action step_finished
   #21 act step_started
   #22 act run_recovered
   #23 act step_started
   #24 act step_finished
   #25 finalize step_started
   #26 finalize step_finished
```
