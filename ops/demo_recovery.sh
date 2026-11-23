#!/usr/bin/env bash
# 崩溃恢复演示，两个中断点：
#   A. 停在等人批准时 kill -9（进程在 gate 上）
#   B. 副作用已经写下去、step_finished 还没落盘时硬退出（最坏情况）
# 用法：bash ops/demo_recovery.sh   （输出可 tee 到 docs/RECOVERY-DEMO.md）
set -u
PORT=${PORT:-8010}
BASE="http://127.0.0.1:$PORT"
PY=.venv/bin/python

say() { printf '\n=== %s ===\n' "$*"; }
jq() { $PY -c "import sys,json;d=json.load(sys.stdin);print($1)"; }
cleanup() { lsof -ti tcp:$PORT 2>/dev/null | xargs kill -9 2>/dev/null; }
boot() { $PY -m uvicorn app.api.server:app --port $PORT >/tmp/recover.log 2>&1 & sleep 8; }

cleanup; sleep 1

say "A1. 启动服务"
boot
PID=$(lsof -ti tcp:$PORT | head -1)
echo "uvicorn pid=$PID"

say "A2. 提一个需要人批准的问题（停在 gate_action）"
RUN=$(curl -s -X POST $BASE/api/runs -H 'content-type: application/json' \
  -d '{"question":"博士毕业落户能加多少分","profile":{"degree":"phd"}}')
echo "$RUN" | jq '"run_id="+d["run_id"], "status="+d["status"], "step="+d["step"], "gate="+str(d["gate"]["kind"])'
RID=$(echo "$RUN" | jq 'd["run_id"]')

say "A3. kill -9（不给收尾机会）"
kill -9 "$PID"; sleep 2
curl -s -m 2 $BASE/ >/dev/null 2>&1 || echo "端口已关闭"

say "A4. 重启（新进程，同一个 SQLite）"
boot
echo "新 pid=$(lsof -ti tcp:$PORT | head -1)"

say "A5. run 还在，停在同一步"
curl -s "$BASE/api/runs/$RID?events=1" | jq '"status="+d["status"], "step="+d["step"], "gate="+str(d["gate"]["kind"]), "events="+str(len(d["events"]))'

say "A6. 用原 run_id 继续"
curl -s -X POST "$BASE/api/runs/$RID/resume" -H 'content-type: application/json' \
  -d '{"action":"approve","by":"demo"}' | $PY -c 'import sys,json;d=json.load(sys.stdin);r=d["action"]["result"];print("status="+d["status"],"applied=%s reused=%s"%(r["applied"],r["reused"]))'

say "A7. 同一个 run 再批一次（409，不能重复写）"
CODE=$(curl -s -o /tmp/second.json -w '%{http_code}' -X POST "$BASE/api/runs/$RID/resume" \
  -H 'content-type: application/json' -d '{"action":"approve"}')
echo "HTTP $CODE  $(cat /tmp/second.json)"

say "B1. 最坏中断点：act 写完待办、step_finished 之前硬退出"
cleanup; sleep 1
$PY -m tools.demo.crash_mid_act 2>/dev/null
SRID=$(sqlite3 data/runtime.db "select run_id from runs where step='act' and status='running' order by rowid desc limit 1")
echo "stuck run=$SRID"

say "B2. 重启后它的状态"
boot
curl -s "$BASE/api/runs/$SRID" | jq '"status="+d["status"], "step="+d["step"], "gate="+str(d["gate"])'

say "B3. 恢复（重入 act，副作用按条幂等）"
curl -s -X POST "$BASE/api/runs/$SRID/resume" -H 'content-type: application/json' \
  -d '{"action":"recover"}' | $PY -c 'import sys,json;d=json.load(sys.stdin);r=d["action"]["result"];print("status="+d["status"],"applied=%s reused=%s keys=%s"%(r["applied"],r["reused"],len(r["keys"])))'

say "B4. 这个 run 的待办条数（应当仍是 3）"
sqlite3 data/runtime.db "select count(*) from actions where run_id='$SRID'"

say "A8/B5. 事件链（run_recovered 是恢复留下的痕迹）"
curl -s "$BASE/api/runs/$SRID?events=1" | $PY -c 'import sys,json;d=json.load(sys.stdin);print("\n".join("  #%s %s %s"%(e["seq"],e["step"],e["kind"]) for e in d["events"][-8:]))'

cleanup
