const state = {
  filter: {
    mode: "preset",
    preset: "today",
    start: "",
    end: "",
  },
  selectedHistoryUserId: "",
  selectedHistoryConversationId: "",
  analyticsUserQuery: "",
  loadingCount: 0,
  charts: {
    requestTrend: null,
    coreRequestTrend: null,
    deviceQuality: null,
    modeDistribution: null,
    questionFlow: null,
    favoriteRatio: null,
    integrityRisk: null,
    citationCoverage: null,
    userRequestTrend: null,
    userModeDistribution: null,
  },
};

const DASHBOARD_FETCH_TIMEOUT_MS = 18000;
const $ = (id) => document.getElementById(id);
const DOUGHNUT_MIN_LABEL_PCT = 4;

const doughnutPercentLabelPlugin = {
  id: "doughnutPercentLabelPlugin",
  afterDatasetsDraw(chart) {
    if (!chart || chart.config.type !== "doughnut") return;
    const dataset = chart.data?.datasets?.[0];
    const arcs = chart.getDatasetMeta(0)?.data || [];
    if (!dataset || !arcs.length) return;
    const values = (dataset.data || []).map((value) => Math.max(0, Number(value || 0)));
    const total = values.reduce((sum, value) => sum + value, 0);
    if (!(total > 0)) return;

    const ctx = chart.ctx;
    ctx.save();
    ctx.font = '700 11px "M PLUS 1p","Zen Kaku Gothic New","Noto Sans JP",sans-serif';
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "rgba(8, 22, 40, 0.42)";
    ctx.lineWidth = 3;
    arcs.forEach((arc, index) => {
      const value = values[index] || 0;
      if (!(value > 0)) return;
      const pct = (value / total) * 100;
      if (pct < DOUGHNUT_MIN_LABEL_PCT) return;
      const props = arc.getProps(["x", "y", "startAngle", "endAngle", "innerRadius", "outerRadius"], true);
      const mid = (props.startAngle + props.endAngle) / 2;
      const radius = props.innerRadius + (props.outerRadius - props.innerRadius) * 0.57;
      const x = props.x + Math.cos(mid) * radius;
      const y = props.y + Math.sin(mid) * radius;
      const text = `${pct.toFixed(1)}%`;
      ctx.strokeText(text, x, y);
      ctx.fillText(text, x, y);
    });
    ctx.restore();
  },
};

const HELP = {
  dau: {
    tech: "24時間以内に更新があった一意ユーザー数。",
    biz: "今日、実際に利用したユーザー人数。",
  },
  wau: {
    tech: "直近7日以内に更新があった一意ユーザー数。",
    biz: "週次の実利用ユーザー規模。",
  },
  activeUsersInWindow: {
    tech: "選択期間内で更新イベントを持つ一意ユーザー数。",
    biz: "この期間に動いたユーザー人数。",
  },
  activeSessionStickiness: {
    tech: "メッセージ総数 / 活性会話総数（ウィンドウ内）。",
    biz: "1会話あたり会話継続の深さ。",
  },
  conversationMessageVolume: {
    tech: "会話総数とメッセージ総数の同時表示。",
    biz: "利用量の全体ボリューム。",
  },
  firstAnswerAvgMs: {
    tech: "`/v2/ask` 系成功リクエストの平均レイテンシ。",
    biz: "初回回答までの平均待ち時間。",
  },
  enhanceAnswerAvgMs: {
    tech: "`/v2/ask/enhance_full` 系成功リクエストの平均レイテンシ。",
    biz: "回答強化処理の平均待ち時間。",
  },
  followupOpenSuccessRate: {
    tech: "S/R、S=追問チェーン成功数、R=追問認識数。",
    biz: "追問と判定後に文脈継続できた割合。",
  },
  feedbackLikeRate: {
    tech: "高評価フィードバック件数 / 全フィードバック件数。",
    biz: "回答満足度の概況。",
  },
  querySuggestStableRate: {
    tech: "入力候補結果ログにおける安定ステージ比率。",
    biz: "入力候補が安定生成できている割合。",
  },
  querySuggestAvgLatencyMs: {
    tech: "入力候補ログの平均応答時間（秒）。",
    biz: "候補表示の体感速度。",
  },
  messageFailureRate: {
    tech: "エラー状態またはエラーメッセージ付きメッセージの比率。",
    biz: "会話途中で失敗したメッセージの割合。",
  },
  citationCoverageRate: {
    tech: "根拠情報付きアシスタントメッセージ数 / アシスタント全件数。",
    biz: "回答に根拠が付いている割合。",
  },
  restoreSuccessRate: {
    tech: "履歴復元成功イベント合計 / 復元関連イベント総数。",
    biz: "履歴同期の復元成功率。",
  },
  requestCore: {
    tech: "総リクエスト数とコア業務リクエスト数を併記。",
    biz: "業務処理トラフィックの規模感。",
  },
  error5xxRate: {
    tech: "5xx 件数 / 総リクエスト件数。",
    biz: "サーバー障害の発生率。",
  },
  requestP95LatencyMs: {
    tech: "HTTP レイテンシ分布の95パーセンタイル。",
    biz: "遅い体験側の代表値。",
  },
  requestTrend: {
    tech: "時間バケットごとのリクエスト数をパソコン/モバイルで分解。",
    biz: "アクセス量の時間推移。",
  },
  coreRequestTrend: {
    tech: "時間バケットごとのコア業務リクエスト推移。",
    biz: "主要機能の利用推移。",
  },
  deviceQuality: {
    tech: "端末区分別の件数と5xx率を同時表示。",
    biz: "パソコンとモバイルの品質差。",
  },
  modeDistribution: {
    tech: "送信時モードを基準としたモード別件数分布。",
    biz: "どのモードが使われているか。",
  },
  questionFlow: {
    tech: "ユーザーメッセージの新規質問と追問の構成比。",
    biz: "新規質問と追問の利用比率。",
  },
  favoriteConversation: {
    tech: "お気に入り会話数 / 会話総数。",
    biz: "保存価値がある会話の割合。",
  },
  integrityRisk: {
    tech: "整合性状態がリスク判定の会話比率。",
    biz: "会話整合性リスクの割合。",
  },
  citationCoverage: {
    tech: "根拠付きアシスタントメッセージ比率を可視化。",
    biz: "回答の根拠提示レベル。",
  },
  topEndpoints: {
    tech: "5xx 発生件数の多いエンドポイント上位。",
    biz: "障害影響が大きいAPI箇所。",
  },
  topMessageErrors: {
    tech: "メッセージ単位のエラー理由上位。",
    biz: "現場で多い失敗理由。",
  },
  userRequestTrend: {
    tech: "選択ユーザーのリクエスト推移（端末分解）。",
    biz: "個別ユーザーの利用リズム。",
  },
  userModeDistribution: {
    tech: "選択ユーザーのモード利用分布。",
    biz: "個別ユーザーの操作傾向。",
  },
};

function fmtInt(value) {
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "-";
}

function fmtPct(value) {
  const num = Number(value);
  return Number.isFinite(num) ? `${(num * 100).toFixed(2)}%` : "-";
}

function fmtMs(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "-";
  return `${(num / 1000).toFixed(2)} s`;
}

function fmtRatio(value, digits = 2) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(digits) : "-";
}

function fmtJst(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const dt = new Date(text);
  if (Number.isNaN(dt.getTime())) return text;
  return dt.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function isChartReady() {
  return typeof window.Chart !== "undefined";
}

function cssVar(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function rgba(hex, alpha) {
  const c = String(hex || "").replace("#", "").trim();
  if (!/^[0-9a-fA-F]{6}$/.test(c)) return `rgba(13, 106, 223, ${alpha})`;
  const r = Number.parseInt(c.slice(0, 2), 16);
  const g = Number.parseInt(c.slice(2, 4), 16);
  const b = Number.parseInt(c.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function toast(message) {
  const el = $("toast");
  if (!el) return;
  el.textContent = String(message || "");
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

function nowJstClock() {
  return new Date().toLocaleTimeString("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour12: false,
  });
}

function setLoadingStatus(message, status = "idle") {
  const el = $("loadingStatus");
  if (!el) return;
  el.textContent = String(message || "");
  el.classList.toggle("loading", status === "loading");
  el.classList.toggle("error", status === "error");
}

function beginLoading(taskLabel) {
  state.loadingCount += 1;
  setLoadingStatus(`読込中: ${taskLabel}`, "loading");
  let closed = false;
  return ({ message, status = "idle" } = {}) => {
    if (closed) return;
    closed = true;
    state.loadingCount = Math.max(0, state.loadingCount - 1);
    if (state.loadingCount > 0) {
      setLoadingStatus(`読込中: 残り${state.loadingCount}件`, "loading");
      return;
    }
    setLoadingStatus(message || `更新完了 ${nowJstClock()}`, status);
  };
}

function formatDashboardStatus(meta) {
  const payload = meta || {};
  if (payload.cacheHit) {
    return `更新完了（キャッシュ） ${nowJstClock()}`;
  }
  const fetchMs = Number(payload.fetchMs || 0);
  const taskMs = payload.taskMs || {};
  const fsMs = Number(taskMs.fs_metrics || 0);
  const bqMax = Math.max(
    Number(taskMs.bq_overview || 0),
    Number(taskMs.usage_timeseries || 0),
    Number(taskMs.error_report || 0),
    Number(taskMs.device_report || 0),
    Number(taskMs.followup_report || 0),
    Number(taskMs.request_user_rows || 0)
  );
  const parts = [];
  if (fetchMs > 0) parts.push(`サーバー${(fetchMs / 1000).toFixed(2)}s`);
  if (fsMs > 0) parts.push(`Firestore${(fsMs / 1000).toFixed(2)}s`);
  if (bqMax > 0) parts.push(`BigQuery最大${(bqMax / 1000).toFixed(2)}s`);
  return parts.length > 0
    ? `更新完了（${parts.join(" / ")}） ${nowJstClock()}`
    : `更新完了 ${nowJstClock()}`;
}

async function getJson(path) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DASHBOARD_FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(path, { credentials: "include", signal: controller.signal });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status} ${path} ${text.slice(0, 180)}`);
    }
    return await res.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

function buildFilterQueryString() {
  const params = new URLSearchParams();
  if (state.filter.mode === "custom") {
    if (state.filter.start) params.set("start", state.filter.start);
    if (state.filter.end) params.set("end", state.filter.end);
  } else if (state.filter.mode === "preset" && state.filter.preset) {
    params.set("preset", state.filter.preset);
  }
  if (state.analyticsUserQuery.trim()) {
    params.set("user", state.analyticsUserQuery.trim());
  }
  return params.toString();
}

function markActivePresetButton() {
  document.querySelectorAll(".presetBtn").forEach((btn) => {
    const selected = state.filter.mode === "preset" && btn.dataset.preset === state.filter.preset;
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
    toast("開始と終了を入力してください。");
    return false;
  }
  if (end <= start) {
    toast("終了時刻は開始時刻より後にしてください。");
    return false;
  }
  state.filter.mode = "custom";
  state.filter.preset = "";
  state.filter.start = start;
  state.filter.end = end;
  markActivePresetButton();
  return true;
}

function ensureChartTheme() {
  if (!isChartReady()) return;
  const line = cssVar("--line", "#d0deee");
  const muted = cssVar("--muted", "#5f738d");
  window.Chart.defaults.color = muted;
  window.Chart.defaults.borderColor = line;
  window.Chart.defaults.font.family = '"M PLUS 1p","Zen Kaku Gothic New","Noto Sans JP",sans-serif';
  window.Chart.defaults.plugins.legend.labels.usePointStyle = true;
  window.Chart.defaults.plugins.legend.labels.boxWidth = 10;
  window.Chart.defaults.plugins.tooltip.backgroundColor = "rgba(17, 29, 46, 0.94)";
  window.Chart.defaults.plugins.tooltip.titleColor = "#ffffff";
  window.Chart.defaults.plugins.tooltip.bodyColor = "#ffffff";
  window.Chart.defaults.plugins.tooltip.borderWidth = 1;
  window.Chart.defaults.plugins.tooltip.borderColor = "rgba(255,255,255,0.12)";
}

function resetChart(name, config) {
  ensureChartTheme();
  if (state.charts[name]) {
    state.charts[name].destroy();
  }
  state.charts[name] = new Chart(config.ctx, config.options);
}

function metricTitleHtml(title, key) {
  const help = HELP[key] || { tech: "定義未設定", biz: "定義未設定" };
  return `${title}<span class="metricTip">ⓘ<span class="tipBody"><span>技術説明：${help.tech}</span><span>業務説明：${help.biz}</span></span></span>`;
}

function displayMode(mode) {
  const normalized = String(mode || "").trim().toLowerCase();
  const map = {
    internal: "社内",
    websearch: "ウェブ検索",
    deepthinking: "深掘り",
    standard: "標準",
    unknown: "不明",
  };
  return map[normalized] || (normalized ? normalized : "不明");
}

function displayRole(role) {
  const normalized = String(role || "").trim().toLowerCase();
  const map = {
    user: "ユーザー",
    assistant: "アシスタント",
    system: "システム",
  };
  return map[normalized] || (normalized ? normalized : "");
}

function displayStatus(status) {
  const normalized = String(status || "").trim().toLowerCase();
  const map = {
    success: "成功",
    done: "完了",
    completed: "完了",
    pending: "処理中",
    running: "処理中",
    processing: "処理中",
    error: "エラー",
    failed: "失敗",
    timeout: "タイムアウト",
    canceled: "キャンセル",
    cancelled: "キャンセル",
  };
  return map[normalized] || (normalized ? normalized : "");
}

function setMetricTitle(id, title, key) {
  const el = $(id);
  if (!el) return;
  el.innerHTML = metricTitleHtml(title, key);
}

function initMetricTitles() {
  setMetricTitle("titleRequestTrend", "リクエスト推移（パソコン/モバイル）", "requestTrend");
  setMetricTitle("titleCoreRequestTrend", "コアリクエスト推移（パソコン/モバイル）", "coreRequestTrend");
  setMetricTitle("titleDeviceQuality", "デバイス品質比較", "deviceQuality");
  setMetricTitle("titleModeDistribution", "モード利用分布", "modeDistribution");
  setMetricTitle("titleQuestionFlow", "新規質問と追問の構成", "questionFlow");
  setMetricTitle("titleFavoriteRatio", "お気に入り会話占有率", "favoriteConversation");
  setMetricTitle("titleIntegrityRisk", "会話整合性リスク率", "integrityRisk");
  setMetricTitle("titleCitationCoverage", "引用カバレッジ率", "citationCoverage");
  setMetricTitle("titleTopEndpoints", "エラー発生エンドポイント上位", "topEndpoints");
  setMetricTitle("titleTopMessageErrors", "エラー理由 上位N（メッセージ級）", "topMessageErrors");
  setMetricTitle("titleUserRequestTrend", "単一ユーザーのリクエスト推移", "userRequestTrend");
  setMetricTitle("titleUserModeDistribution", "単一ユーザーのモード利用分布", "userModeDistribution");
}

function buildSummaryCards(summary) {
  const cards = [
    { key: "dau", label: "DAU", value: fmtInt(summary.dau) },
    { key: "wau", label: "WAU", value: fmtInt(summary.wau) },
    { key: "activeUsersInWindow", label: "ウィンドウ活性ユーザー", value: fmtInt(summary.activeUsersInWindow) },
    { key: "activeSessionStickiness", label: "活性会話粘着度", value: fmtRatio(summary.activeSessionStickiness, 2) },
    {
      key: "conversationMessageVolume",
      label: "会話総量 / メッセージ総量",
      value: `${fmtInt(summary.conversationCount)} / ${fmtInt(summary.messageCount)}`,
    },
    { key: "firstAnswerAvgMs", label: "初回回答平均時間", value: fmtMs(summary.firstAnswerAvgMs) },
    { key: "enhanceAnswerAvgMs", label: "回答強化平均時間", value: fmtMs(summary.enhanceAnswerAvgMs) },
    { key: "followupOpenSuccessRate", label: "追問開通成功率", value: fmtPct(summary.followupOpenSuccessRate) },
    { key: "feedbackLikeRate", label: "いいね率", value: fmtPct(summary.feedbackLikeRate) },
    { key: "querySuggestStableRate", label: "入力候補安定率", value: fmtPct(summary.querySuggestStableRate) },
    { key: "querySuggestAvgLatencyMs", label: "入力候補平均時間", value: fmtMs(summary.querySuggestAvgLatencyMs) },
    { key: "messageFailureRate", label: "メッセージ失敗率", value: fmtPct(summary.messageFailureRate) },
    { key: "citationCoverageRate", label: "引用カバレッジ率", value: fmtPct(summary.citationCoverageRate) },
    { key: "restoreSuccessRate", label: "同期復元成功率", value: fmtPct(summary.restoreSuccessRate) },
    {
      key: "requestCore",
      label: "総/コアリクエスト",
      value: `${fmtInt(summary.requestCount)} / ${fmtInt(summary.coreRequestCount)}`,
    },
    { key: "error5xxRate", label: "5xxエラー率", value: fmtPct(summary.error5xxRate) },
    { key: "requestP95LatencyMs", label: "要求P95遅延", value: fmtMs(summary.requestP95LatencyMs) },
  ];

  const root = $("kpiCardsPrimary");
  root.innerHTML = "";
  cards.forEach((card) => {
    const article = document.createElement("article");
    article.className = "summaryCard";
    article.innerHTML = `
      <div class="label">${metricTitleHtml(card.label, card.key)}</div>
      <div class="value">${card.value}</div>
    `;
    root.appendChild(article);
  });
}

function normalizeUsageSeries(timeseries) {
  const map = new Map();
  for (const row of timeseries || []) {
    const key = row.bucket_key || row.bucketKey || "";
    if (!key) continue;
    if (!map.has(key)) {
      map.set(key, {
        label: row.bucket_label || row.bucketLabel || key,
        desktop: { total: 0, core: 0, system: 0 },
        mobile: { total: 0, core: 0, system: 0 },
        unknown: { total: 0, core: 0, system: 0 },
      });
    }
    const slot = map.get(key);
    const device = row.device_class || "unknown";
    if (!slot[device]) {
      slot[device] = { total: 0, core: 0, system: 0 };
    }
    const total = Number(row.request_count || 0);
    const core = Number(row.core_request_count || 0);
    const system = Number(row.system_request_count || Math.max(0, total - core));
    slot[device].total = Number.isFinite(total) ? total : 0;
    slot[device].core = Number.isFinite(core) ? core : 0;
    slot[device].system = Number.isFinite(system) ? system : 0;
  }
  return map;
}

function renderLineChart(name, canvasId, labels, datasets) {
  resetChart(name, {
    ctx: $(canvasId),
    options: {
      type: "line",
      data: {
        labels: labels.length ? labels : ["データなし"],
        datasets: labels.length ? datasets : datasets.map((d) => ({ ...d, data: [0] })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { grid: { display: false } },
          y: { beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    },
  });
}

function renderRequestTrend(timeseries, chartName, canvasId, valueKey = "total") {
  const primary = cssVar("--primary", "#0d6adf");
  const mint = cssVar("--mint", "#00a88f");
  const neutral = "#7f93ad";
  const map = normalizeUsageSeries(timeseries);
  const keys = [...map.keys()];
  const labels = keys.map((k) => map.get(k).label || k);
  const desktop = keys.map((k) => map.get(k).desktop[valueKey] || 0);
  const mobile = keys.map((k) => map.get(k).mobile[valueKey] || 0);
  const unknown = keys.map((k) => map.get(k).unknown[valueKey] || 0);

  renderLineChart(chartName, canvasId, labels, [
    {
      label: "パソコン",
      data: desktop,
      borderColor: primary,
      backgroundColor: rgba(primary, 0.14),
      borderWidth: 2.4,
      tension: 0.26,
      fill: false,
      pointRadius: 2,
    },
    {
      label: "モバイル",
      data: mobile,
      borderColor: mint,
      backgroundColor: rgba(mint, 0.14),
      borderWidth: 2.4,
      tension: 0.26,
      fill: false,
      pointRadius: 2,
    },
    {
      label: "不明",
      data: unknown,
      borderColor: neutral,
      backgroundColor: "transparent",
      borderWidth: 1.6,
      borderDash: [6, 4],
      tension: 0.2,
      fill: false,
      pointRadius: 1.6,
    },
  ]);
}

function renderSystemUsageChart(timeseries) {
  renderRequestTrend(timeseries, "coreRequestTrend", "coreRequestTrendChart", "system");
}

function renderDeviceQuality(rows) {
  const primary = cssVar("--primary", "#0d6adf");
  const danger = cssVar("--danger", "#d14a4a");
  const labels = (rows || []).map((row) => {
    if (row.device_class === "desktop") return "パソコン";
    if (row.device_class === "mobile") return "モバイル";
    return "不明";
  });
  const counts = (rows || []).map((row) => Number(row.request_count || 0));
  const errorRates = (rows || []).map((row) => Number(row.error_5xx_rate || 0) * 100);

  resetChart("deviceQuality", {
    ctx: $("deviceQualityChart"),
    options: {
      type: "bar",
      data: {
        labels: labels.length ? labels : ["データなし"],
        datasets: [
          {
            label: "リクエスト件数",
            data: labels.length ? counts : [0],
            yAxisID: "y",
            backgroundColor: rgba(primary, 0.82),
            borderRadius: 10,
            maxBarThickness: 54,
          },
          {
            label: "5xx率(%)",
            data: labels.length ? errorRates : [0],
            type: "line",
            yAxisID: "y1",
            borderColor: danger,
            backgroundColor: danger,
            tension: 0.22,
            pointRadius: 3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          y: { beginAtZero: true, position: "left", ticks: { precision: 0 } },
          y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false } },
        },
      },
    },
  });
}

function renderDoughnutChart(name, canvasId, labels, values, colors) {
  const hasData = (labels || []).length > 0;
  const baseLabels = hasData ? labels : ["データなし"];
  const rawValues = hasData ? (values || []).map((value) => Number(value || 0)) : [0];
  const total = rawValues.reduce((sum, value) => sum + Math.max(0, Number(value || 0)), 0);
  resetChart(name, {
    ctx: $(canvasId),
    options: {
      type: "doughnut",
      plugins: [doughnutPercentLabelPlugin],
      data: {
        labels: baseLabels,
        datasets: [
          {
            data: rawValues,
            backgroundColor: hasData ? colors : ["#dbe7f6"],
            borderWidth: 2,
            borderColor: "#ffffff",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const index = Number(ctx.dataIndex || 0);
                const value = Math.max(0, Number(rawValues[index] || 0));
                const pct = total > 0 ? (value / total) * 100 : 0;
                const label = baseLabels[index] || "";
                return `${label}: ${value.toLocaleString()} (${pct.toFixed(1)}%)`;
              },
            },
          },
        },
        cutout: "62%",
      },
    },
  });
}

function renderModeDistribution(rows, chartName, canvasId) {
  const modeCounts = { internal: 0, websearch: 0 };
  for (const row of rows || []) {
    const mode = String(row.mode || "unknown").toLowerCase();
    if (mode !== "internal" && mode !== "websearch") continue;
    modeCounts[mode] += Number(row.count || 0);
  }
  const labels = ["社内", "ウェブ検索"];
  const values = [modeCounts.internal, modeCounts.websearch];
  renderDoughnutChart(
    chartName,
    canvasId,
    labels,
    values,
    ["#0d6adf", "#00a88f", "#9e6dd6", "#f0a11f", "#8ea0b8"]
  );
}

function setTableRows(tableId, rows, mapper) {
  const tbody = document.querySelector(`#${tableId} tbody`);
  if (!tbody) return;
  tbody.innerHTML = "";
  const list = rows || [];
  if (!list.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = document.querySelectorAll(`#${tableId} thead th`).length || 1;
    td.textContent = "データなし";
    td.className = "muted";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  list.forEach((row) => {
    const tr = document.createElement("tr");
    const cells = mapper(row);
    cells.forEach((cell) => {
      const td = document.createElement("td");
      td.textContent = String(cell ?? "");
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

function renderGlobalCharts(payload) {
  const charts = payload.charts || {};
  renderRequestTrend(charts.requestTrend || [], "requestTrend", "requestTrendChart", "total");
  renderRequestTrend(charts.coreRequestTrend || [], "coreRequestTrend", "coreRequestTrendChart", "core");
  renderDeviceQuality(charts.deviceQuality || []);
  renderModeDistribution(charts.modeDistribution || [], "modeDistribution", "modeDistributionChart");

  const qFlow = charts.questionFlow || {};
  renderDoughnutChart(
    "questionFlow",
    "questionFlowChart",
    ["新規質問", "追問"],
    [Number(qFlow.newQuestionCount || 0), Number(qFlow.followupCount || 0)],
    ["#0d6adf", "#00a88f"]
  );

  const fav = charts.favoriteConversation || {};
  renderDoughnutChart(
    "favoriteRatio",
    "favoriteRatioChart",
    ["お気に入り", "非お気に入り"],
    [Number(fav.count || 0), Math.max(0, Number(fav.total || 0) - Number(fav.count || 0))],
    ["#00a88f", "#d0dceb"]
  );

  const risk = charts.integrityRisk || {};
  renderDoughnutChart(
    "integrityRisk",
    "integrityRiskChart",
    ["リスク", "健全"],
    [Number(risk.count || 0), Math.max(0, Number(risk.total || 0) - Number(risk.count || 0))],
    ["#d14a4a", "#cad8eb"]
  );

  const citation = charts.citationCoverage || {};
  renderDoughnutChart(
    "citationCoverage",
    "citationCoverageChart",
    ["引用あり", "引用なし"],
    [
      Number(citation.covered || 0),
      Math.max(0, Number(citation.assistantTotal || 0) - Number(citation.covered || 0)),
    ],
    ["#0d6adf", "#d3dfef"]
  );

  setTableRows("topEndpointsTable", charts.topErrorEndpoints || [], (row) => [
    row.endpoint || "",
    fmtInt(row.error_5xx_count),
  ]);
  setTableRows("topMessageErrorsTable", charts.topMessageErrors || [], (row) => [
    row.errorReason || "不明",
    fmtInt(row.count),
  ]);
}

function renderUserMetricCards(selectedUser) {
  const root = $("userMetricCards");
  root.innerHTML = "";
  if (!selectedUser) {
    const card = document.createElement("article");
    card.className = "summaryCard";
    card.innerHTML = `<div class="label">ユーザー未選択</div><div class="value">-</div>`;
    root.appendChild(card);
    return;
  }

  const cards = [
    {
      key: "activeSessionStickiness",
      label: "活性会話粘着度",
      value: fmtRatio(selectedUser.activeSessionStickiness, 2),
    },
    {
      key: "conversationMessageVolume",
      label: "会話総量 / メッセージ総量",
      value: `${fmtInt(selectedUser.conversationCount)} / ${fmtInt(selectedUser.messageCount)}`,
    },
    {
      key: "feedbackLikeRate",
      label: "いいね率",
      value: fmtPct(selectedUser.feedbackLikeRate),
    },
    {
      key: "requestCore",
      label: "総/コアリクエスト",
      value: `${fmtInt(selectedUser.totalRequestCount)} / ${fmtInt(selectedUser.coreRequestCount)}`,
    },
    {
      key: "deviceQuality",
      label: "端末利用比率（パソコン/モバイル）",
      value: `${fmtPct(selectedUser.desktopRequestRate)} / ${fmtPct(selectedUser.mobileRequestRate)}`,
    },
    {
      key: "questionFlow",
      label: "新規質問と追問",
      value: `${fmtInt(selectedUser.newQuestionCount)} / ${fmtInt(selectedUser.followupCount)}`,
    },
    {
      key: "followupOpenSuccessRate",
      label: "追問開通成功率",
      value: fmtPct(selectedUser.followupOpenSuccessRate),
    },
    {
      key: "citationCoverageRate",
      label: "引用カバレッジ率",
      value: fmtPct(selectedUser.citationCoverageRate),
    },
  ];

  cards.forEach((card) => {
    const article = document.createElement("article");
    article.className = "summaryCard";
    article.innerHTML = `
      <div class="label">${metricTitleHtml(card.label, card.key)}</div>
      <div class="value">${card.value}</div>
    `;
    root.appendChild(article);
  });
}

function renderUserTable(users) {
  const tbody = document.querySelector("#userMetricsTable tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  const rows = users || [];
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.className = "muted";
    td.textContent = "データなし";
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    const values = [
      row.userId || "",
      row.userEmail || "",
      `${fmtInt(row.conversationCount)} / ${fmtInt(row.messageCount)}`,
      fmtRatio(row.activeSessionStickiness, 2),
      fmtPct(row.feedbackLikeRate),
      `${fmtInt(row.totalRequestCount)} / ${fmtInt(row.coreRequestCount)}`,
      fmtPct(row.followupOpenSuccessRate),
      fmtPct(row.citationCoverageRate),
    ];
    values.forEach((value) => {
      const td = document.createElement("td");
      td.textContent = String(value ?? "");
      tr.appendChild(td);
    });
    tr.addEventListener("click", () => {
      state.analyticsUserQuery = String(row.userId || row.userEmail || "");
      $("analyticsUserSearch").value = state.analyticsUserQuery;
      switchTab("user");
      reloadDashboard();
    });
    tbody.appendChild(tr);
  });
}

function renderSelectedUser(payload) {
  const selected = payload.selectedUser || null;
  const timeseries = payload.selectedUserTimeseries || [];
  const requestMetricsReady = Boolean(payload.selectedUserRequestMetricsReady);
  const labelEl = $("selectedAnalyticsUserLabel");

  if (!selected) {
    labelEl.textContent = "単一ユーザー未選択";
    renderUserMetricCards(null);
    renderRequestTrend([], "userRequestTrend", "userRequestTrendChart", "total");
    renderModeDistribution([], "userModeDistribution", "userModeDistributionChart");
    return;
  }

  labelEl.textContent = `対象: ${selected.userEmail || ""} (${selected.userId || ""})${
    requestMetricsReady ? "" : " / ユーザー別リクエスト埋点待ち"
  }`;
  renderUserMetricCards(selected);
  renderRequestTrend(requestMetricsReady ? timeseries : [], "userRequestTrend", "userRequestTrendChart", "total");
  renderModeDistribution(selected.modeDistribution || [], "userModeDistribution", "userModeDistributionChart");
}

function updateSummaryWindow(payload) {
  const windowPayload = payload.window || {};
  const start = fmtJst(windowPayload.start || "");
  const end = fmtJst(windowPayload.end || "");
  $("summaryWindowLabel").textContent = `${start} - ${end}`;
}

function updateExportLinks() {
  const exportUsers = $("exportUsers");
  const exportConversations = $("exportConversations");

  if (state.selectedHistoryUserId) {
    exportUsers.href = `/api/export/users.csv?user_id=${encodeURIComponent(state.selectedHistoryUserId)}&include_hidden=true`;
    exportUsers.classList.remove("disabled");
  } else {
    exportUsers.href = "#";
    exportUsers.classList.add("disabled");
  }

  if (state.selectedHistoryUserId && state.selectedHistoryConversationId) {
    exportConversations.href = `/api/export/conversations.csv?user_id=${encodeURIComponent(state.selectedHistoryUserId)}&conversation_id=${encodeURIComponent(state.selectedHistoryConversationId)}`;
    exportConversations.classList.remove("disabled");
  } else {
    exportConversations.href = "#";
    exportConversations.classList.add("disabled");
  }
}

function switchTab(tab) {
  const global = tab === "global";
  $("tabGlobal").classList.toggle("active", global);
  $("tabUser").classList.toggle("active", !global);
  $("paneGlobal").classList.toggle("active", global);
  $("paneUser").classList.toggle("active", !global);
}

async function reloadDashboard() {
  if (!isChartReady()) {
    toast("グラフライブラリの読み込みに失敗しました。");
    return;
  }

  const done = beginLoading("ダッシュボード");
  try {
    const query = buildFilterQueryString();
    const settled = await Promise.allSettled([getJson(`/api/metrics/dashboard?${query}`)]);
    if (settled[0].status !== "fulfilled") {
      throw settled[0].reason;
    }
    const payload = settled[0].value;
    buildSummaryCards(payload.summary || {});
    updateSummaryWindow(payload);
    renderGlobalCharts(payload);
    renderUserTable(payload.users || []);
    renderSelectedUser(payload);
    done({ message: formatDashboardStatus(payload.meta || {}), status: "idle" });
  } catch (error) {
    console.error(error);
    toast("一部データの取得に失敗しました。");
    toast(`ダッシュボード取得失敗: ${String(error)}`);
    done({ message: `ダッシュボード取得失敗 ${nowJstClock()}`, status: "error" });
  }
}

function appendCells(tr, values) {
  values.forEach((value) => {
    const td = document.createElement("td");
    td.textContent = String(value ?? "");
    tr.appendChild(td);
  });
}

async function loadUsers() {
  const done = beginLoading("ユーザー一覧");
  try {
    const q = encodeURIComponent($("userSearch").value.trim());
    const payload = await getJson(`/api/history/users?limit=250&q=${q}`);
    const rows = payload.users || [];
    const tbody = document.querySelector("#usersTable tbody");
    tbody.innerHTML = "";

    rows.forEach((row) => {
      const tr = document.createElement("tr");
      appendCells(tr, [row.userId || "", row.userEmail || "", row.updatedAtJst || fmtJst(row.updatedAt || "")]);
      tr.addEventListener("click", () => {
        state.selectedHistoryUserId = row.userId || "";
        state.selectedHistoryConversationId = "";
        [...tbody.querySelectorAll("tr")].forEach((item) => item.classList.remove("selected"));
        tr.classList.add("selected");
        $("messageMeta").textContent = "メッセージ履歴";
        updateExportLinks();
        loadConversations();
      });
      tbody.appendChild(tr);
    });

    updateExportLinks();
    done({ message: `ユーザー一覧更新 ${fmtInt(rows.length)}件 ${nowJstClock()}`, status: "idle" });
  } catch (error) {
    toast(`ユーザー検索失敗: ${String(error)}`);
    done({ message: `ユーザー一覧取得失敗 ${nowJstClock()}`, status: "error" });
  }
}

async function loadConversations() {
  if (!state.selectedHistoryUserId) {
    toast("先にユーザーを選択してください。");
    return;
  }
  const done = beginLoading("会話一覧");
  try {
    const q = encodeURIComponent($("convSearch").value.trim());
    const payload = await getJson(
      `/api/history/users/${encodeURIComponent(state.selectedHistoryUserId)}/conversations?limit=400&q=${q}`
    );
    const rows = payload.conversations || [];
    const tbody = document.querySelector("#conversationsTable tbody");
    tbody.innerHTML = "";

    rows.forEach((row) => {
      const tr = document.createElement("tr");
      appendCells(tr, [row.id || "", row.title || "", row.updatedAtJst || fmtJst(row.updatedAt || "")]);
      tr.addEventListener("click", () => {
        state.selectedHistoryConversationId = row.id || "";
        [...tbody.querySelectorAll("tr")].forEach((item) => item.classList.remove("selected"));
        tr.classList.add("selected");
        $("messageMeta").textContent = "メッセージ履歴";
        updateExportLinks();
        loadMessages();
      });
      tbody.appendChild(tr);
    });

    updateExportLinks();
    done({ message: `会話一覧更新 ${fmtInt(rows.length)}件 ${nowJstClock()}`, status: "idle" });
  } catch (error) {
    toast(`会話検索失敗: ${String(error)}`);
    done({ message: `会話一覧取得失敗 ${nowJstClock()}`, status: "error" });
  }
}

async function loadMessages() {
  if (!state.selectedHistoryUserId || !state.selectedHistoryConversationId) {
    toast("先に会話を選択してください。");
    return;
  }
  const done = beginLoading("メッセージ履歴");
  try {
    const payload = await getJson(
      `/api/history/users/${encodeURIComponent(state.selectedHistoryUserId)}/conversations/${encodeURIComponent(state.selectedHistoryConversationId)}?limit=1500`
    );
    setTableRows("messagesTable", payload.messages || [], (row) => [
      row.timestampJst || fmtJst(row.timestamp || ""),
      displayRole(row.role || ""),
      displayMode(row.modeAtSend || ""),
      row.questionKind === "followup" ? "追問" : row.questionKind === "new" ? "新規" : "",
      displayStatus(row.status || ""),
      (row.content || "").replace(/\s+/g, " ").slice(0, 420),
      row.errorMessage || "",
    ]);
    done({
      message: `メッセージ履歴更新 ${fmtInt((payload.messages || []).length)}件 ${nowJstClock()}`,
      status: "idle",
    });
  } catch (error) {
    toast(`メッセージ取得失敗: ${String(error)}`);
    done({ message: `メッセージ履歴取得失敗 ${nowJstClock()}`, status: "error" });
  }
}

function bindEvents() {
  $("refreshAll").addEventListener("click", () => reloadDashboard());
  $("applyCustom").addEventListener("click", () => {
    if (applyCustomFilter()) reloadDashboard();
  });

  document.querySelectorAll(".presetBtn").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyPresetFilter(btn.dataset.preset || "today");
      reloadDashboard();
    });
  });

  $("tabGlobal").addEventListener("click", () => switchTab("global"));
  $("tabUser").addEventListener("click", () => switchTab("user"));

  $("searchAnalyticsUser").addEventListener("click", () => {
    state.analyticsUserQuery = $("analyticsUserSearch").value.trim();
    switchTab("user");
    reloadDashboard();
  });

  $("analyticsUserSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      state.analyticsUserQuery = $("analyticsUserSearch").value.trim();
      switchTab("user");
      reloadDashboard();
    }
  });

  $("loadUsers").addEventListener("click", () => loadUsers());
  $("loadConversations").addEventListener("click", () => loadConversations());

  $("userSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadUsers();
  });

  $("convSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadConversations();
  });

  document.querySelectorAll("a.btnLink").forEach((link) => {
    link.addEventListener("click", (event) => {
      if (link.classList.contains("disabled") || link.getAttribute("href") === "#") {
        event.preventDefault();
      }
    });
  });
}

function init() {
  setLoadingStatus("待機中", "idle");
  markActivePresetButton();
  initMetricTitles();
  bindEvents();
  reloadDashboard();
  loadUsers();
  updateExportLinks();
}

init();
