const state = {
  days: 7,
  filter: {
    mode: "preset",
    preset: "today",
    start: "",
    end: "",
  },
  selectedUserId: "",
  selectedConversationId: "",
  chartThemeApplied: false,
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
const fmtMs = (v) => (typeof v === "number" && Number.isFinite(v) ? `${Math.round(v).toLocaleString()} ms` : "-");

function isChartReady() {
  return typeof window.Chart !== "undefined";
}

function cssVar(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function rgba(hex, alpha) {
  const c = String(hex || "").replace("#", "").trim();
  if (!/^[0-9a-fA-F]{6}$/.test(c)) return `rgba(30, 97, 219, ${alpha})`;
  const r = Number.parseInt(c.slice(0, 2), 16);
  const g = Number.parseInt(c.slice(2, 4), 16);
  const b = Number.parseInt(c.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function ensureChartTheme() {
  if (!isChartReady() || state.chartThemeApplied) return;
  const text = cssVar("--text", "#152033");
  const muted = cssVar("--muted", "#5d6e84");
  const line = cssVar("--line", "#d8e2f1");
  window.Chart.defaults.color = muted;
  window.Chart.defaults.borderColor = line;
  window.Chart.defaults.font.family = '"Noto Sans JP","Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif';
  window.Chart.defaults.plugins.legend.labels.usePointStyle = true;
  window.Chart.defaults.plugins.legend.labels.boxHeight = 8;
  window.Chart.defaults.plugins.legend.labels.boxWidth = 10;
  window.Chart.defaults.plugins.tooltip.backgroundColor = "rgba(17, 24, 39, 0.92)";
  window.Chart.defaults.plugins.tooltip.titleColor = "#ffffff";
  window.Chart.defaults.plugins.tooltip.bodyColor = "#ffffff";
  window.Chart.defaults.plugins.tooltip.borderColor = rgba(text.replace("#", ""), 0.18);
  window.Chart.defaults.plugins.tooltip.borderWidth = 1;
  state.chartThemeApplied = true;
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
  const primary = [
    { label: "DAU", value: fmtInt(usage.dau) },
    { label: "WAU", value: fmtInt(usage.wau) },
    { label: "リクエスト数", value: fmtInt(overview.request_count) },
    { label: "会話数 / メッセージ数", value: `${fmtInt(usage.conversationCount)} / ${fmtInt(usage.messageCount)}` },
    { label: "初回回答平均時間", value: fmtMs(overview.first_answer_avg_ms) },
    { label: "回答強化平均時間", value: fmtMs(overview.enhance_answer_avg_ms) },
  ];

  const secondary = [
    { label: "入力候補安定生成率", value: fmtPct(overview.qs_stable_rate) },
    { label: "回答フィードバックいいね率", value: fmtPct(usage.feedbackLikeRate) },
    { label: "5xx エラー率", value: fmtPct(overview.error_5xx_rate) },
    { label: "P95 レイテンシ", value: fmtMs(overview.request_p95_latency_ms) },
  ];

  const renderCards = (rootId, cards) => {
    const root = $(rootId);
    root.textContent = "";
    for (const card of cards) {
      const wrapper = document.createElement("div");
      wrapper.className = "card";
      const label = document.createElement("div");
      label.className = "label";
      label.textContent = card.label;
      const value = document.createElement("div");
      value.className = "value";
      value.textContent = String(card.value ?? "-");
      wrapper.appendChild(label);
      wrapper.appendChild(value);
      root.appendChild(wrapper);
    }
  };

  renderCards("kpiCardsPrimary", primary);
  renderCards("kpiCardsSecondary", secondary);
}

function buildFilterQueryString() {
  const params = new URLSearchParams();
  if (state.filter.mode === "custom") {
    if (state.filter.start) params.set("start", state.filter.start);
    if (state.filter.end) params.set("end", state.filter.end);
  } else if (state.filter.mode === "preset" && state.filter.preset) {
    params.set("preset", state.filter.preset);
  } else {
    params.set("days", String(state.days || 7));
  }
  return params.toString();
}

function markActivePresetButton() {
  document.querySelectorAll(".presetBtn").forEach((btn) => {
    const selected = btn.dataset.preset === state.filter.preset && state.filter.mode === "preset";
    btn.classList.toggle("active", selected);
  });
}

function applyPresetFilter(preset) {
  state.filter.mode = "preset";
  state.filter.preset = preset;
  state.filter.start = "";
  state.filter.end = "";
  $("startAt").value = "";
  $("endAt").value = "";
  markActivePresetButton();
}

function applyCustomFilter() {
  const start = $("startAt").value.trim();
  const end = $("endAt").value.trim();
  if (!start || !end) {
    toast("カスタム期間では開始と終了の両方を指定してください。");
    return false;
  }
  if (end <= start) {
    toast("終了は開始より後の時刻を指定してください。");
    return false;
  }
  state.filter.mode = "custom";
  state.filter.preset = "";
  state.filter.start = start;
  state.filter.end = end;
  markActivePresetButton();
  return true;
}

function initFilterUi() {
  markActivePresetButton();
}

function resetChart(name, config) {
  if (!isChartReady()) return;
  ensureChartTheme();
  if (state.charts[name]) {
    state.charts[name].destroy();
  }
  state.charts[name] = new Chart(config.ctx, config.options);
}

function renderUsageChart(timeseries) {
  const primary = cssVar("--primary", "#1e61db");
  const accent = cssVar("--accent", "#02a678");
  const neutral = "#94a3b8";
  const map = new Map();
  for (const row of timeseries || []) {
    const key = row.bucket_key || row.bucketKey || row.day || "";
    if (!key) continue;
    if (!map.has(key)) {
      map.set(key, {
        label: row.bucket_label || row.bucketLabel || key,
        desktop: 0,
        mobile: 0,
        unknown: 0,
      });
    }
    const slot = map.get(key);
    if (row.bucket_label || row.bucketLabel) {
      slot.label = row.bucket_label || row.bucketLabel;
    }
    slot[row.device_class || "unknown"] = Number(row.request_count || 0);
  }

  const keys = [...map.keys()];
  const labels = keys.map((k) => map.get(k).label || k);
  const desktop = keys.map((k) => map.get(k).desktop || 0);
  const mobile = keys.map((k) => map.get(k).mobile || 0);
  const unknown = keys.map((k) => map.get(k).unknown || 0);
  const canvas = $("usageChart");
  const ctx = canvas.getContext("2d");
  const desktopFill = ctx ? (() => {
    const g = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 320);
    g.addColorStop(0, rgba(primary, 0.26));
    g.addColorStop(1, rgba(primary, 0.02));
    return g;
  })() : rgba(primary, 0.16);
  const mobileFill = ctx ? (() => {
    const g = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 320);
    g.addColorStop(0, rgba(accent, 0.22));
    g.addColorStop(1, rgba(accent, 0.02));
    return g;
  })() : rgba(accent, 0.14);

  resetChart("usage", {
    ctx: canvas,
    options: {
      type: "line",
      data: {
        labels: labels.length ? labels : ["データなし"],
        datasets: [
          {
            label: "PC",
            data: labels.length ? desktop : [0],
            borderColor: primary,
            backgroundColor: desktopFill,
            fill: true,
            borderWidth: 2.2,
            tension: 0.28,
            pointRadius: 2.2,
            pointHoverRadius: 4,
          },
          {
            label: "モバイル",
            data: labels.length ? mobile : [0],
            borderColor: accent,
            backgroundColor: mobileFill,
            fill: true,
            borderWidth: 2.2,
            tension: 0.28,
            pointRadius: 2.2,
            pointHoverRadius: 4,
          },
          {
            label: "不明",
            data: labels.length ? unknown : [0],
            borderColor: neutral,
            backgroundColor: "transparent",
            fill: false,
            borderDash: [5, 4],
            borderWidth: 1.6,
            tension: 0.24,
            pointRadius: 1.8,
            pointHoverRadius: 3.2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "bottom" },
          tooltip: { intersect: false },
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            grid: { color: rgba("#8ea0bc", 0.2) },
            ticks: { precision: 0 },
          },
        },
      },
    },
  });
}

function renderErrorTrendChart(trendRows) {
  const danger = cssVar("--danger", "#d14343");
  const labels = (trendRows || []).map((r) => r.bucket_label || r.bucketLabel || r.day);
  const values = (trendRows || []).map((r) => Number(r.error_5xx_count || 0));
  const canvas = $("errorTrendChart");
  const ctx = canvas.getContext("2d");
  const fill = ctx ? (() => {
    const g = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 320);
    g.addColorStop(0, rgba(danger, 0.9));
    g.addColorStop(1, rgba(danger, 0.45));
    return g;
  })() : danger;

  resetChart("errorTrend", {
    ctx: canvas,
    options: {
      type: "bar",
      data: {
        labels: labels.length ? labels : ["データなし"],
        datasets: [{ label: "5xx", data: labels.length ? values : [0], backgroundColor: fill, borderRadius: 8 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    },
  });
}

function renderDeviceChart(rows) {
  const deviceLabelMap = { desktop: "PC", mobile: "モバイル", unknown: "不明" };
  const primary = cssVar("--primary", "#1e61db");
  const danger = cssVar("--danger", "#d14343");
  const labels = (rows || []).map((r) => deviceLabelMap[r.device_class] || "不明");
  const req = (rows || []).map((r) => Number(r.request_count || 0));
  const errRate = (rows || []).map((r) => Number(r.error_5xx_rate || 0) * 100);

  resetChart("device", {
    ctx: $("deviceChart"),
    options: {
      type: "bar",
      data: {
        labels: labels.length ? labels : ["データなし"],
        datasets: [
          {
            label: "リクエスト数",
            data: labels.length ? req : [0],
            yAxisID: "y",
            backgroundColor: rgba(primary, 0.8),
            borderRadius: 10,
            maxBarThickness: 48,
          },
          {
            label: "5xx エラー率(%)",
            data: labels.length ? errRate : [0],
            yAxisID: "y1",
            type: "line",
            borderColor: danger,
            backgroundColor: danger,
            tension: 0.2,
            pointRadius: 3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { position: "left", beginAtZero: true, ticks: { precision: 0 } },
          y1: { position: "right", beginAtZero: true, grid: { drawOnChartArea: false } },
        },
      },
    },
  });
}

function renderQsStageChart(rows) {
  const palette = ["#1e61db", "#02a678", "#f59e0b", "#d14343", "#7c3aed"];
  let labels = (rows || []).map((r) => r.stage || "unknown");
  let values = (rows || []).map((r) => Number(r.count || 0));
  if (!labels.length) {
    labels = ["データなし"];
    values = [1];
  }

  resetChart("qsStage", {
    ctx: $("qsStageChart"),
    options: {
      type: "doughnut",
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: labels.map((_, i) => palette[i % palette.length]),
          borderWidth: 2,
          borderColor: "#ffffff",
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: "bottom" } },
        cutout: "64%",
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

function appendCells(tr, values) {
  for (const value of values) {
    const td = document.createElement("td");
    td.textContent = String(value ?? "");
    tr.appendChild(td);
  }
}

function updateExportLinks() {
  const rangeQuery = buildFilterQueryString();
  $("exportUsage").href = `/api/export/usage.csv?${rangeQuery}`;
  $("exportErrorsTrend").href = `/api/export/errors/trend.csv?${rangeQuery}`;
  $("exportErrorsEndpoints").href = `/api/export/errors/endpoints.csv?${rangeQuery}`;
  $("exportErrorsTypes").href = `/api/export/errors/types.csv?${rangeQuery}`;
  $("exportDevices").href = `/api/export/devices.csv?${rangeQuery}`;
  $("exportQsStages").href = `/api/export/query-suggest/stages.csv?${rangeQuery}`;
  $("exportQsFallbacks").href = `/api/export/query-suggest/fallbacks.csv?${rangeQuery}`;
  $("exportQsFacts").href = `/api/export/query-suggest/facts.csv?${rangeQuery}`;

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
  if (!isChartReady()) {
    toast("グラフ描画ライブラリの読み込みに失敗しました。管理者へご連絡ください。");
    return;
  }

  try {
    const rangeQuery = buildFilterQueryString();
    const [overview, usage, errors, devices, querySuggest] = await Promise.all([
      getJson(`/api/metrics/overview?${rangeQuery}`),
      getJson(`/api/metrics/usage?${rangeQuery}`),
      getJson(`/api/metrics/errors?${rangeQuery}`),
      getJson(`/api/metrics/devices?${rangeQuery}`),
      getJson(`/api/metrics/query-suggest?${rangeQuery}`),
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
      appendCells(tr, [row.userId || "", row.userEmail || "", row.updatedAt || ""]);
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
      appendCells(tr, [row.id || "", row.title || "", row.updatedAt || ""]);
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
  document.querySelectorAll(".presetBtn").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyPresetFilter(btn.dataset.preset || "today");
      reloadDashboard();
    });
  });
  $("applyCustom").addEventListener("click", () => {
    if (applyCustomFilter()) {
      reloadDashboard();
    }
  });
  $("loadUsers").addEventListener("click", () => loadUsers());
  $("loadConversations").addEventListener("click", () => loadConversations());
  $("loadMessages").addEventListener("click", () => loadMessages());

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

initFilterUi();
bindEvents();
reloadDashboard();
loadUsers();
