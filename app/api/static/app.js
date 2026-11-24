/* 界面只做三件事：提问、渲染卡片、提交审批。所有判断都在服务端。 */
const $ = (s) => document.querySelector(s);
let current = null;

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, body) {
  const r = await fetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  } : undefined);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function coverageLine(state) {
  const c = state.coverage || {};
  if (!c.status) return "覆盖范围：未知";
  const ok = c.status === "covered";
  return `${ok ? "已接入" : c.status} · ${c.city || ""}${ok ? "（可检索）" : "（不可检索）"}`;
}

function renderEvidence(list, limit = 3) {
  if (!list || !list.length) return `<div class="muted">—</div>`;
  const shown = list.slice(0, limit);
  const rest = list.length - shown.length;
  const one = (e) => `
    <div class="ev">
      <div class="cite">${esc(e.citation)}</div>
      <blockquote class="clamp">${esc(e.quote)}</blockquote>
      <div class="meta">
        ${e.page ? `<span class="tag">第 ${e.page} 页</span>` : `<span class="tag">网页原文</span>`}
        ${e.verified === true ? `<span class="tag ok">逐字核验通过</span>` : ""}
        ${e.stale ? `<span class="tag warn">${esc(e.stale)}</span>` : ""}
      </div>
    </div>`;
  return `<div class="rows">${shown.map(one).join("")}
    ${rest > 0 ? `<button class="more" data-rest="${rest}">展开其余 ${rest} 条</button>
      <div class="hidden-rows">${list.slice(limit).map(one).join("")}</div>` : ""}
  </div>`;
}

function wireMore(root) {
  root.querySelectorAll(".more").forEach((b) => {
    b.onclick = () => {
      const box = b.nextElementSibling;
      box.classList.toggle("open");
      b.textContent = box.classList.contains("open") ? "收起" : `展开其余 ${b.dataset.rest} 条`;
    };
  });
  root.querySelectorAll("blockquote.clamp").forEach((q) => {
    q.onclick = () => q.classList.toggle("open");
  });
}

function bar(score) {
  const { known, upper, standard, gap } = score || {};
  if (standard == null) return "";
  const pct = (x) => Math.max(0, Math.min(100, (x / standard) * 100));
  return `
  <div class="score">
    <div class="bar-track">
      ${upper != null ? `<div class="bar-upper" style="width:${pct(upper)}%"></div>` : ""}
      ${known != null ? `<div class="bar-fill" style="width:${pct(known)}%"></div>` : ""}
      <div class="bar-std" style="left:calc(100% - 2px)"></div>
    </div>
    <div class="score-num">已知 ${known ?? "?"} / 上限 ${upper ?? "?"} / 标准 ${standard}${gap ? ` · 差 ${gap}` : ""}</div>
  </div>`;
}

function renderVerdict(c) {
  const direct = c.verdict === "direct";
  return `
  <div class="card">
    <div class="card-head"><span class="badge verdict">判定</span>
      <div class="headline">${esc(c.headline)}</div></div>
    <div class="card-body">
      ${renderValidity(c)}
      ${direct ? `<div class="muted" style="margin:10px 0">直落条件，不适用评分办法。</div>` : bar(c.score)}
      ${direct ? "" : `<div class="row"><div class="k">依据</div><div class="v">
        <ul class="plain">${(c.basis || []).map((b) =>
          `<li>${esc(b.label)} ${b.points != null ? `+${b.points}` : ""}
           ${b.page ? `<span class="tag">第 ${b.page} 页</span>` : ""}</li>`).join("") || "<li class='muted'>—</li>"}</ul>
      </div></div>`}
      ${(c.missing || []).length ? `<div class="row"><div class="k">待确认</div>
        <div class="v">${c.missing.slice(0, 12).map((m) => `<span class="tag q">${esc(m)}</span>`).join(" ")}
        ${c.missing.length > 12 ? `<span class="muted">等 ${c.missing.length} 项</span>` : ""}</div></div>` : ""}
      <div class="row"><div class="k">未覆盖</div>
        <div class="v muted">${(c.not_claimed || []).map(esc).join("、") || "—"}</div></div>
      <div class="row"><div class="k">原文</div><div class="v">${renderEvidence(c.evidence)}</div></div>
      ${renderDraft(c)}
    </div>
  </div>`;
}

function renderValidity(c) {
  const vs = c.validity || [];
  if (!vs.length) return "";
  return `<div class="validity"><b>时效提醒</b>（基准日 ${esc(vs[0].as_of)}）` +
    vs.map((v) => `<div>《${esc(v.doc_no)}》${v.status === "expired" ? "已失效" :
      v.status === "not_yet" ? "尚未施行" : "状态未知"}：${esc(v.reason)}</div>`).join("") +
    `</div>`;
}

function renderStandard(c) {
  return `
  <div class="card">
    <div class="card-head"><span class="badge standard">标准</span>
      <div class="headline">${esc(c.headline)}</div></div>
    <div class="card-body">
      ${renderValidity(c)}
      <div class="row"><div class="k">取决于</div>
        <div class="v muted">${(c.depends_on || []).map(esc).join("、")}</div></div>
      <div class="row"><div class="k">原文</div><div class="v">${renderEvidence(c.items && c.items.length ? c.items : c.evidence)}</div></div>
      <div class="row"><div class="k">未覆盖</div>
        <div class="v muted">${(c.not_claimed || []).map(esc).join("、") || "—"}</div></div>
      ${renderDraft(c)}
    </div>
  </div>`;
}

function renderRefusal(c) {
  return `
  <div class="card">
    <div class="card-head"><span class="badge refusal">拒答</span>
      <div class="headline">${esc(c.headline)}</div></div>
    <div class="card-body">
      <div class="row"><div class="k">原因</div><div class="v mono">${esc(c.reason_kind)}</div></div>
      <div class="row"><div class="k">检索范围</div><div class="v">
        ${c.searched?.n_docs ?? 0} 份文件 · ${c.searched?.n_queries ?? 0} 条候选条款</div></div>
      ${c.next ? `<div class="row"><div class="k">下一步</div><div class="v">${esc(c.next)}</div></div>` : ""}
      <div class="row"><div class="k">候选</div><div class="v">${renderEvidence(c.evidence)}</div></div>
    </div>
  </div>`;
}

function renderDraft(c) {
  const d = c.draft;
  if (!d) return "";
  const rows = (d.sentences || []).map((s) => `<li>${esc(s.text)}
      ${(s.cites || []).map((e) => `<span class="tag cite">${esc(e)}</span>`).join("")}</li>`).join("");
  const drop = d.dropped
    ? `<span class="warn">丢弃 ${d.dropped} 句（引用或数字对不上证据）</span>` : "";
  const miss = (d.not_covered || []).length
    ? `<div class="muted">未覆盖：${esc((d.not_covered || []).join("；"))}</div>` : "";
  return `<div class="draft">
      <div class="draft-h">模型解读 <span class="muted">只解释证据，不改变上面的判定</span></div>
      <ul class="plain">${rows}</ul>
      ${miss}
      <div class="draft-f muted">${esc(d.model)} · ${d.cached ? "缓存" : "本次调用"} ·
        置信度 ${esc(d.confidence || "?")} ${drop}</div>
    </div>`;
}

function renderCard(c) {
  if (!c) return `<div class="card"><div class="card-body muted">没有卡片（流程未到 compose）</div></div>`;
  if (c.kind === "verdict") return renderVerdict(c);
  if (c.kind === "standard") return renderStandard(c);
  return renderRefusal(c);
}

function renderGate(g, runId, state) {
  if (!g && state && state.status === "running") {
    // 进程被 kill 掉后重新打开：库里留着 status=running / step=中断点
    $("#gate").innerHTML = `
      <div class="gate recover">
        <h2>上次进程中断</h2>
        <div class="q">这个 run 停在 <b>${esc(state.step)}</b> 步。状态在库里，
          会从这一步重入；已完成的步骤不会重跑，写过的待办按条幂等。</div>
        <div class="acts">
          <button class="ok" data-act="recover">继续这个 run</button>
        </div>
      </div>`;
    bindGate($("#gate"), runId);
    return;
  }
  if (!g) { $("#gate").innerHTML = ""; return; }
  const isAction = g.kind === "action";
  const q = isAction ? "这次操作会产生外部副作用（写待办）。批准后才执行。" : "判定为不确定，需要人确认。";
  const items = (g.payload && g.payload.action && g.payload.action.items) || [];
  const list = items.length
    ? `<ul class="plain">${items.map((i) => `<li>${esc(i.text)}
        <span class="tag">${esc(i.doc_no)}</span></li>`).join("")}</ul>`
    : "";
  $("#gate").innerHTML = `
    <div class="gate">
      <h2>${isAction ? "待批准：写入办理计划" : "待确认：判定不确定"}</h2>
      <div class="q">${q}</div>
      ${list}
      <div class="acts">
        <button class="ok" data-act="${isAction ? "approve" : "confirm_unknown"}">批准</button>
        <button class="no" data-act="${isAction ? "deny" : "reject"}">拒绝</button>
      </div>
    </div>`;
  bindGate($("#gate"), runId);
}

function bindGate(box, runId) {
  box.querySelectorAll("button").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      const s = await api(`/api/runs/${runId}/resume`, { action: b.dataset.act, by: "ui" });
      render(s);
      const full = await api(`/api/runs/${runId}?events=1`);
      renderTimeline(full.events);
      loadRuns();
    };
  });
}

function renderTimeline(events) {
  if (!events) return;
  const dots = { step_finished: "done", step_failed: "fail" };
  $("#timeline").innerHTML = events.map((e) => `
    <li class="${dots[e.kind] || ""}">${esc(e.step)} · ${esc(e.kind)}
      <span class="t">#${e.seq}</span></li>`).join("") || `<li class="muted">—</li>`;
  const box = $("#timeline"); box.scrollTop = box.scrollHeight;
}

function render(state) {
  current = state;
  const qline = state.question
    ? `<div class="qline">问：${esc(state.question)}${state.year ? `　<span class="muted">（判定基准 ${state.year} 年）</span>` : ""}</div>`
    : "";
  $("#result").innerHTML = `<div class="cardwrap">${qline}${renderCard(state.answer && state.answer.card)}</div>`;
  wireMore($("#result"));
  renderGate(state.gate, state.run_id, state);
  $("#coverage").textContent = coverageLine(state);
  if (state.run_id) {
    $("#runmeta").textContent = `run ${state.run_id.slice(0, 8)} · ${state.status} · ${state.step}`;
  }
}

async function loadRuns() {
  try {
    const runs = await api("/api/runs");
    $("#runs").innerHTML = (runs || []).slice(0, 8).map((r) => `
      <li data-id="${esc(r.run_id)}">${esc((r.question || "").slice(0, 34))}
        <span class="tag">${esc(r.status)}</span></li>`).join("") || `<li class="muted">—</li>`;
    $("#runs").querySelectorAll("li[data-id]").forEach((li) => {
      li.onclick = async () => {
        const s = await api(`/api/runs/${li.dataset.id}?events=1`);
        render(s); renderTimeline(s.events);
      };
    });
  } catch (e) { /* 列表失败不影响主流程 */ }
}

$("#ask").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  const btn = $(".go"); btn.disabled = true; btn.textContent = "查询中…";
  try {
    let profile = {};
    try { profile = JSON.parse($("#profile").value || "{}"); } catch { profile = {}; }
    const s = await api("/api/runs", { question: q, profile });
    render(s);
    const full = await api(`/api/runs/${s.run_id}?events=1`);
    renderTimeline(full.events);
    loadRuns();
  } catch (e) {
    $("#result").innerHTML = `<div class="card"><div class="card-body">
      <div class="tag bad">请求失败</div><div class="muted">${esc(e.message)}</div></div></div>`;
  } finally { btn.disabled = false; btn.textContent = "查询"; }
});

document.querySelectorAll(".chip").forEach((c) => {
  c.onclick = () => {
    $("#q").value = c.dataset.q;
    if (c.dataset.p) $("#profile").value = c.dataset.p;
    $("#ask").requestSubmit();
  };
});

loadRuns();
