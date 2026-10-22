"use strict";

const state = {
  payload: null,
  selectedId: null,
  mode: "static",
  config: null,
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function appendInlineText(container, text) {
  const parts = String(text).split("**");
  parts.forEach((part, index) => {
    if (!part) return;
    if (index % 2 === 1) container.append(el("strong", null, part));
    else container.append(document.createTextNode(part));
  });
}

function statusClass(status) {
  const s = String(status || "").toLowerCase();
  if (s === "ok") return "status-ok";
  if (s === "empty") return "status-empty";
  if (s === "degraded") return "status-degraded";
  if (s === "error" || s === "denied" || s === "dead_letter") return "status-error";
  return "status-skipped";
}

function statusText(status) {
  const s = String(status || "").toLowerCase();
  const map = {
    ok: "ok",
    empty: "empty",
    degraded: "degraded",
    error: "error",
    denied: "denied",
    dead_letter: "dead_letter",
    skipped: "skipped",
  };
  return map[s] || String(status || "—");
}

function pct(value) {
  return `${Math.round(Number(value) * 100)}%`;
}

function renderSummary(payload) {
  const s = payload.summary;
  const failureKinds = Object.keys(s.failure_kinds || {}).join(" / ") || "无";
  const cards = [
    {
      value: `${s.success}/${s.tickets}`,
      label: "端到端成功",
      hint: `${s.failed} 个失败，分类 ${failureKinds}`,
    },
    {
      value: pct(s.context_tokens.saved_pct_mean),
      label: "上下文 token 节省",
      hint: `${s.context_tokens.uncompacted_sum} → ${s.context_tokens.compacted_sum}`,
    },
    {
      value: `${s.pinned}/${s.tickets}`,
      label: "pinned 约束保留",
      hint: "压缩后仍在上下文里",
    },
    {
      value: `${(s.tool_verdicts && s.tool_verdicts.denied) || 0}`,
      label: "工具被拒",
      hint: "白名单在链路里真的行使了",
    },
    {
      value: `${(s.failure_kinds && s.failure_kinds.silent_empty_answer) || 0}`,
      label: "静默空回答",
      hint: "HTTP 200 但 content 为空，被分类",
    },
  ];
  const root = $("summary");
  root.replaceChildren();
  for (const card of cards) {
    const node = el("div", "metric-card");
    node.append(el("span", "metric-value", card.value));
    node.append(el("span", "metric-label", card.label));
    node.append(el("span", "metric-hint", card.hint));
    root.append(node);
  }
}

function ticketStatus(item) {
  if (item.failure) return "error";
  if (item.llm_status === "empty") return "empty";
  if (item.llm_status === "degraded") return "degraded";
  if (item.tool_verdict === "denied" || item.tool_verdict === "dead_letter") return "error";
  return "ok";
}

function renderTickets() {
  const root = $("ticket-list");
  root.replaceChildren();
  for (const item of state.payload.items) {
    const button = el(
      "button",
      "ticket-button" + (item.id === state.selectedId ? " is-selected" : "")
    );
    button.type = "button";
    button.dataset.ticketId = item.id;
    const top = el("div", "ticket-top");
    top.append(el("span", "ticket-id", item.id));
    const status = ticketStatus(item);
    top.append(el("span", `status ${statusClass(status)}`, statusText(status)));
    button.append(top);
    button.append(el("p", "ticket-text", item.text));
    button.addEventListener("click", () => {
      state.selectedId = item.id;
      renderTickets();
      renderDetail();
    });
    root.append(button);
  }
}

function selectedItem() {
  if (!state.payload || !state.payload.items || !state.payload.items.length) {
    return null;
  }
  return (
    state.payload.items.find((item) => item.id === state.selectedId) ||
    state.payload.items[0]
  );
}

function renderDetail() {
  const item = selectedItem();
  if (!item) return;
  state.selectedId = item.id;
  $("detail-title").textContent = `链路 · ${item.id}`;
  $("detail-scenario").textContent = item.scenario || "";
  $("ticket-text").textContent = item.text;

  const timeline = $("stage-timeline");
  timeline.replaceChildren();
  for (const [index, stage] of (item.stages || []).entries()) {
    const row = el("div", "stage");
    row.append(el("div", "stage-index", String(index + 1)));
    const body = el("div", "stage-body");
    const head = el("div", "stage-head");
    head.append(el("span", "stage-name", stage.name));
    head.append(
      el("span", `status ${statusClass(stage.status)}`, statusText(stage.status))
    );
    body.append(head);
    body.append(el("p", "stage-detail", stage.detail || ""));
    const metrics = el("div", "metrics");
    for (const metric of stage.metrics || []) {
      const chip = el("span", "metric-chip");
      chip.append(document.createTextNode(`${metric.label}: `));
      chip.append(el("strong", null, metric.value));
      metrics.append(chip);
    }
    body.append(metrics);
    row.append(body);
    timeline.append(row);
  }

  const answer = $("answer");
  answer.replaceChildren();
  answer.className = "answer " + (item.answer ? "is-ok" : "is-empty");
  answer.append(
    el("p", "answer-title", item.answer ? "助手回答" : "助手回答为空")
  );
  answer.append(
    el(
      "p",
      "answer-text",
      item.answer ||
        "(空回答：HTTP 200 但 content 为空；端到端记为 silent_empty_answer)"
    )
  );
  if (item.flags && item.flags.length) {
    answer.append(el("p", "stage-detail", `flags: ${item.flags.join(", ")}`));
  }

  renderEvidence(item);
  $("raw-json").textContent = JSON.stringify(item, null, 2);
}

function renderEvidence(item) {
  const root = $("evidence");
  root.replaceChildren();
  const table = el("table", "evidence-table");
  const thead = el("thead");
  const headRow = el("tr");
  headRow.append(el("th", null, "字段"));
  headRow.append(el("th", null, "值"));
  headRow.append(el("th", null, "来源"));
  thead.append(headRow);
  table.append(thead);

  const body = el("tbody");
  const rows = [];
  for (const stage of item.stages || []) {
    for (const metric of stage.metrics || []) {
      rows.push({
        field: `${stage.name} · ${metric.label}`,
        value: metric.value,
        source: metric.source,
      });
    }
  }
  rows.push({
    field: "failure",
    value: item.failure || "—",
    source: "m7_e2e.TicketResult.failure",
  });
  rows.push({
    field: "端到端耗时",
    value: `${item.latency_ms} ms`,
    source: "m7_e2e.TicketResult.latency_ms",
  });
  rows.push({
    field: "回答长度",
    value: item.answer ? `${item.answer.length} 字` : "0 字",
    source: "gateway_request.content",
  });

  for (const row of rows) {
    const tr = el("tr");
    tr.append(el("td", null, row.field));
    tr.append(el("td", null, String(row.value)));
    tr.append(el("td", "evidence-source", row.source));
    body.append(tr);
  }
  table.append(body);
  root.append(table);
}

function renderBoundaries(payload) {
  const root = $("boundaries");
  root.replaceChildren();
  for (const boundary of payload.boundaries || []) {
    const item = el("li");
    appendInlineText(item, boundary);
    root.append(item);
  }
}

function renderMeta(payload) {
  const meta = payload.meta || {};
  const verification = payload.verification || {};
  const rows = [
    ["仓库", meta.repo],
    ["Demo", meta.demo],
    ["提交", meta.commit],
    ["工作区", meta.worktree_dirty ? "有未提交改动" : "干净（results/ 生成物除外）"],
    ["生成时间 (UTC)", meta.generated_at],
    ["模式", meta.mode],
    ["seed", meta.seed],
    ["model", meta.model],
    ["Python", meta.python],
    ["与 M7 结果对账", `${verification.status || "—"} (${verification.checks || 0} checks)`],
  ];
  if (meta.dirty_paths && meta.dirty_paths.length) {
    rows.push(["未提交路径", meta.dirty_paths.join(", ")]);
  }
  const dl = $("meta");
  dl.replaceChildren();
  for (const [key, value] of rows) {
    dl.append(el("dt", null, key));
    dl.append(el("dd", null, value === undefined || value === null ? "—" : value));
  }
}

function renderMode() {
  const badge = $("mode-badge");
  badge.className = "badge " + (state.mode === "live" ? "badge-live" : "badge-static");
  badge.textContent = state.mode === "live" ? "本地实时" : "静态回放";
  $("mode-note").textContent =
    state.mode === "live"
      ? "点击“重跑整批”会重新执行 M7 离线链路；数字随之更新。"
      : "这是提交进仓库的一次确定性离线运行。运行 python3 demo/server.py 可实时重算。";
  $("rerun").disabled = state.mode !== "live";
}

function renderAll() {
  renderSummary(state.payload);
  renderTickets();
  renderDetail();
  renderBoundaries(state.payload);
  renderMeta(state.payload);
  renderMode();
}

async function loadConfig() {
  try {
    const response = await fetch("./config.json", { cache: "no-store" });
    if (response.ok) return await response.json();
  } catch (error) {
    // file:// or a static host without config.json: stay in replay mode.
  }
  return { mode: "static" };
}

async function rerun() {
  const button = $("rerun");
  const status = $("rerun-status");
  button.disabled = true;
  status.textContent = "正在重跑 M7 离线链路…";
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.payload = payload;
    if (!payload.items.some((item) => item.id === state.selectedId)) {
      state.selectedId = payload.items[0] ? payload.items[0].id : null;
    }
    renderAll();
    status.textContent = `重跑完成 · ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    status.textContent = `重跑失败：${error.message}`;
  } finally {
    button.disabled = state.mode !== "live";
  }
}

async function init() {
  try {
    state.config = await loadConfig();
    state.mode = state.config.mode === "live" ? "live" : "static";
    const response = await fetch("./runs.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`runs.json HTTP ${response.status}`);
    state.payload = await response.json();
    state.selectedId = state.payload.items[0] ? state.payload.items[0].id : null;
    renderAll();

    $("rerun").addEventListener("click", rerun);
    $("raw-toggle").addEventListener("click", () => {
      const pre = $("raw-json");
      const button = $("raw-toggle");
      const wasHidden = pre.hidden;
      pre.hidden = !wasHidden;
      button.setAttribute("aria-expanded", String(wasHidden));
      button.textContent = wasHidden ? "收起原始 JSON" : "查看本条原始 JSON";
    });
  } catch (error) {
    const box = $("error");
    box.hidden = false;
    box.textContent =
      `加载失败：${error.message}。如果你在用 file:// 打开页面，` +
      "请运行 python3 demo/server.py 或 python3 -m http.server -d demo/public。";
  }
}

document.addEventListener("DOMContentLoaded", init);
