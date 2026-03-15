const state = {
  days: 7,
  selectedUserId: "",
  selectedConversationId: "",
  charts: {
    usage: null,
    errorTrend: null,
    device: null,
    qsStage: null,
  },
};

const $ = (id) => document.getElementById(id);

const fmtInt = (v) => (typeof v === "number" && Number.isFinite(v) ? v.toLocaleString() : "-");
const fmtPct = (v) => (typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(2)}%` : "-");

function isChartReady() {
  return typeof window.Chart !== "undefined";
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

async function getJson(path) {
  const res = await fetch(path, { credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${path} ${text.slice(0, 180)}`);
  }
  return res.json();
}

function buildCards(overview, usage) {
  const cards = [
    { label: "リクエスト数", value: fmtInt(overview.request_count) },
    { label: "5xx エラー率", value: fmtPct(overview.error_5xx_rate) },
    { label: "P95 レイテンシ(ms)", value: fmtInt(overview.request_p95_latency_ms) },
    { label: "入力候補安定生成率", value: fmtPct(overview.qs_stable_rate) },
    { label: "日次/週次アクティブユーザー（DAU/WAU）", value: `${fmtInt(usage.dau)} / ${fmtInt(usage.wau)}` },
    { label: "会話数 / メッセージ数", value: `${fmtInt(usage.conversationCount)} / ${fmtInt(usage.messageCount)}` },
    { label: "履歴復元成功率", value: fmtPct(overview.restore_success_rate) },
    { label: "QS 平均候補数", value: fmtInt(overview.qs_avg_suggestion_count) },
  ];

  $("kpiCards").innerHTML = cards
    .map((c) => `<div class="card"><div class="label">${c.label}</div><div class="value">${c.value}</div></div>`)
    .join("");
}

function resetChart(name, config) {
  if (!isChartReady()) return;
  if (state.charts[name]) {
    state.charts[name].destroy();
  }
  state.charts[name] = new Chart(config.ctx, config.options);
}

function renderUsageChart(timeseries) {
  const map = new Map();
  for (const row of timeseries || []) {
    const day = row.day;
    if (!map.has(day)) {
      map.set(day, { desktop: 0, mobile: 0, unknown: 0 });
    }
    const slot = map.get(day);
    slot[row.device_class || "unknown"] = Number(row.request_count || 0);
  }

  const labels = [...map.keys()];
  const desktop = labels.map((d) => map.get(d).desktop || 0);
  const mobile = labels.map((d) => map.get(d).mobile || 0);
  const unknown = labels.map((d) => map.get(d).unknown || 0);

  resetChart("usage", {
    ctx: $("usageChart"),
    options: {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "PC", data: desktop, borderColor: "#1664e2", backgroundColor: "#1664e2", tension: 0.2 },
          { label: "モバイル", data: mobile, borderColor: "#00a56a", backgroundColor: "#00a56a", tension: 0.2 },
          { label: "不明", data: unknown, borderColor: "#94a3b8", backgroundColor: "#94a3b8", tension: 0.2 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } },
      },
    },
  });
}

function renderErrorTrendChart(trendRows) {
  const labels = (trendRows || []).map((r) => r.day);
  const values = (trendRows || []).map((r) => Number(r.error_5xx_count || 0));

  resetChart("errorTrend", {
    ctx: $("errorTrendChart"),
    options: {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "5xx", data: values, backgroundColor: "#d64545" }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } },
      },
    },
  });
}

function renderDeviceChart(rows) {
  const deviceLabelMap = { desktop: "PC", mobile: "モバイル", unknown: "不明" };
  const labels = (rows || []).map((r) => deviceLabelMap[r.device_class] || "不明");
  const req = (rows || []).map((r) => Number(r.request_count || 0));
  const errRate = (rows || []).map((r) => Number(r.error_5xx_rate || 0) * 100);

  resetChart("device", {
    ctx: $("deviceChart"),
    options: {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "リクエスト数", data: req, yAxisID: "y", backgroundColor: "#1664e2" },
          { label: "5xx エラー率(%)", data: errRate, yAxisID: "y1", type: "line", borderColor: "#d64545", backgroundColor: "#d64545", tension: 0.2 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { position: "left" },
          y1: { position: "right", grid: { drawOnChartArea: false } },
        },
      },
    },
  });
}

function renderQsStageChart(rows) {
  const labels = (rows || []).map((r) => r.stage || "unknown");
  const values = (rows || []).map((r) => Number(r.count || 0));

  resetChart("qsStage", {
    ctx: $("qsStageChart"),
    options: {
      type: "doughnut",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: ["#1664e2", "#f59e0b", "#00a56a", "#a855f7"] }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } },
      },
    },
  });
}

function setTableRows(tableId, rows, mapper) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  tbody.innerHTML = "";
  for (const row of rows || []) {
    const tr = document.createElement("tr");
    for (const cell of mapper(row)) {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

function updateExportLinks() {
  const d = encodeURIComponent(String(state.days));
  $("exportUsage").href = `/api/export/usage.csv?days=${d}`;
  $("exportErrorsTrend").href = `/api/export/errors/trend.csv?days=${d}`;
  $("exportErrorsEndpoints").href = `/api/export/errors/endpoints.csv?days=${d}`;
  $("exportErrorsTypes").href = `/api/export/errors/types.csv?days=${d}`;
  $("exportDevices").href = `/api/export/devices.csv?days=${d}`;
  $("exportQsStages").href = `/api/export/query-suggest/stages.csv?days=${d}`;
  $("exportQsFallbacks").href = `/api/export/query-suggest/fallbacks.csv?days=${d}`;
  $("exportQsFacts").href = `/api/export/query-suggest/facts.csv?days=${d}`;

  const userQ = encodeURIComponent($("userSearch").value.trim());
  $("exportUsers").href = `/api/export/users.csv?limit=500&q=${userQ}`;

  if (state.selectedUserId) {
    const convQ = encodeURIComponent($("convSearch").value.trim());
    const href = `/api/export/conversations.csv?user_id=${encodeURIComponent(state.selectedUserId)}&limit=500&q=${convQ}`;
    $("exportConversations").href = href;
    $("exportConversations").classList.remove("disabled");
  } else {
    $("exportConversations").href = "#";
    $("exportConversations").classList.add("disabled");
  }

  if (state.selectedUserId && state.selectedConversationId) {
    const href = `/api/export/messages.csv?user_id=${encodeURIComponent(state.selectedUserId)}&conversation_id=${encodeURIComponent(state.selectedConversationId)}&limit=2000`;
    $("exportMessages").href = href;
    $("exportMessages").classList.remove("disabled");
  } else {
    $("exportMessages").href = "#";
    $("exportMessages").classList.add("disabled");
  }
}

async function reloadDashboard() {
  state.days = Math.max(1, Math.min(180, Number($("days").value || 7)));
  $("days").value = String(state.days);

  if (!isChartReady()) {
    toast("グラフ描画ライブラリの読み込みに失敗しました。管理者へご連絡ください。");
    return;
  }

  try {
    const [overview, usage, errors, devices, querySuggest] = await Promise.all([
      getJson(`/api/metrics/overview?days=${state.days}`),
      getJson(`/api/metrics/usage?days=${state.days}`),
      getJson(`/api/metrics/errors?days=${state.days}`),
      getJson(`/api/metrics/devices?days=${state.days}`),
      getJson(`/api/metrics/query-suggest?days=${state.days}`),
    ]);

    buildCards(overview.overview || {}, overview.usage || {});
    renderUsageChart(usage.timeseries || []);
    renderErrorTrendChart(errors.trend || []);
    renderDeviceChart(devices.devices || []);
    renderQsStageChart((querySuggest.logs || {}).stages || []);

    setTableRows("topEndpointsTable", errors.topEndpoints || [], (r) => [r.endpoint || "", fmtInt(r.error_5xx_count)]);
    setTableRows("topErrorsTable", errors.topErrors || [], (r) => [r.error_type || "", fmtInt(r.count)]);
    setTableRows("qsFallbackTable", (querySuggest.logs || {}).fallbackSources || [], (r) => [r.fallback_source || "", r.reason || "", fmtInt(r.count)]);

    const facts = querySuggest.facts || {};
    const factRows = Object.entries(facts).map(([k, v]) => ({ metric: k, value: typeof v === "number" ? (k.toLowerCase().includes("rate") ? fmtPct(v) : fmtInt(v)) : String(v) }));
    setTableRows("qsFactsTable", factRows, (r) => [r.metric, r.value]);

    updateExportLinks();
  } catch (err) {
    console.error(err);
    toast(`読み込みに失敗しました: ${String(err)}`);
  }
}

async function loadUsers() {
  try {
    const q = encodeURIComponent($("userSearch").value.trim());
    const payload = await getJson(`/api/history/users?limit=200&q=${q}`);
    const rows = payload.users || [];
    const tbody = document.querySelector("#usersTable tbody");
    tbody.innerHTML = "";

    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${row.userId || ""}</td><td>${row.userEmail || ""}</td><td>${row.updatedAt || ""}</td>`;
      tr.addEventListener("click", () => {
        state.selectedUserId = row.userId || "";
        state.selectedConversationId = "";
        [...tbody.querySelectorAll("tr")].forEach((r) => r.classList.remove("selected"));
        tr.classList.add("selected");
        $("messageMeta").textContent = `選択中ユーザー: ${state.selectedUserId}`;
        updateExportLinks();
        loadConversations();
      });
      tbody.appendChild(tr);
    }
    updateExportLinks();
  } catch (err) {
    toast(`ユーザー検索に失敗しました: ${String(err)}`);
  }
}

async function loadConversations() {
  if (!state.selectedUserId) {
    toast("先にユーザーを選択してください。");
    return;
  }

  try {
    const q = encodeURIComponent($("convSearch").value.trim());
    const payload = await getJson(`/api/history/users/${encodeURIComponent(state.selectedUserId)}/conversations?limit=300&q=${q}`);
    const rows = payload.conversations || [];
    const tbody = document.querySelector("#conversationsTable tbody");
    tbody.innerHTML = "";

    for (const row of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${row.id || ""}</td><td>${row.title || ""}</td><td>${row.updatedAt || ""}</td>`;
      tr.addEventListener("click", () => {
        state.selectedConversationId = row.id || "";
        [...tbody.querySelectorAll("tr")].forEach((r) => r.classList.remove("selected"));
        tr.classList.add("selected");
        $("messageMeta").textContent = `ユーザーID=${state.selectedUserId} / 会話ID=${state.selectedConversationId}`;
        updateExportLinks();
        loadMessages();
      });
      tbody.appendChild(tr);
    }
    updateExportLinks();
  } catch (err) {
    toast(`会話検索に失敗しました: ${String(err)}`);
  }
}

async function loadMessages() {
  if (!state.selectedUserId || !state.selectedConversationId) {
    toast("先に会話を選択してください。");
    return;
  }
  try {
    const payload = await getJson(`/api/history/users/${encodeURIComponent(state.selectedUserId)}/conversations/${encodeURIComponent(state.selectedConversationId)}?limit=800`);
    const rows = payload.messages || [];
    setTableRows("messagesTable", rows, (r) => [r.timestamp || "", r.role || "", (r.content || "").replace(/\s+/g, " ").slice(0, 280)]);
  } catch (err) {
    toast(`メッセージ取得に失敗しました: ${String(err)}`);
  }
}

function bindEvents() {
  $("refreshAll").addEventListener("click", () => reloadDashboard());
  $("loadUsers").addEventListener("click", () => loadUsers());
  $("loadConversations").addEventListener("click", () => loadConversations());
  $("loadMessages").addEventListener("click", () => loadMessages());

  $("days").addEventListener("change", () => {
    updateExportLinks();
  });

  $("userSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadUsers();
  });

  $("convSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadConversations();
  });

  document.querySelectorAll("a.btnLink").forEach((a) => {
    a.addEventListener("click", (e) => {
      if (a.classList.contains("disabled") || a.getAttribute("href") === "#") {
        e.preventDefault();
      }
    });
  });
}

bindEvents();
reloadDashboard();
loadUsers();
