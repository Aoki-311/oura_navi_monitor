import {
  getSystemDashboard,
  getTraceMessages,
  getUserDetail,
  getUsers,
  timeRangeQuery,
} from "./api/client.js";
import { toDashboardViewModel } from "./adapters/dashboardAdapter.js";
import { toMessageRows, toUserDetailViewModel, toUserRows } from "./adapters/usersAdapter.js";
import { displayCount, displayDateTime, displayRate, truncateMiddle } from "./viewModels/formatters.js";

const $ = (id) => document.getElementById(id);
const DASHBOARD_FETCH_TIMEOUT_MS = 18000;
const LEGACY_DASHBOARD_FIELD_COMPAT = "core_request_count";

const state = {
  dashboardPreset: "today",
  usagePreset: "last_7d",
  activityPreset: "last_7d",
  userFilter: "",
  userQuery: "",
  userRows: [],
  usersPage: 1,
  selectedUserId: "",
  selectedConversationId: "",
  messageCursor: "",
  includeMessageContent: false,
  charts: {},
};

const COLORS = {
  blue: "#2563eb",
  teal: "#0ea5a4",
  green: "#059669",
  amber: "#d97706",
  red: "#dc2626",
  slate: "#64748b",
  violet: "#7c3aed",
  cyan: "#0891b2",
};

const USERS_PAGE_SIZE = 10;

function toast(message) {
  const el = $("toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 2600);
}

function setStatus(message, tone = "idle") {
  const el = $("loadingStatus");
  if (!el) return;
  el.textContent = message;
  el.dataset.tone = tone;
}

function currentUserDetailId() {
  return new URLSearchParams(window.location.search).get("user_id") || "";
}

async function reloadCurrentView() {
  const userId = currentUserDetailId();
  if (userId) {
    await openUserDetail(userId);
    return;
  }
  await loadAll();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function destroyChart(id) {
  if (state.charts[id]) {
    state.charts[id].destroy();
    state.charts[id] = null;
  }
}

function chartColors(count) {
  const palette = ["#23d28f", "#ffb340", "#386dff", "#5f6285", "#27d9d2", "#7c5cff", "#ff5b74"];
  return Array.from({ length: count }, (_, index) => palette[index % palette.length]);
}

function createChart(id, config) {
  if (!window.Chart) return;
  destroyChart(id);
  const canvas = $(id);
  if (!canvas) return;
  state.charts[id] = new Chart(canvas, config);
}

function chartTextColor() {
  return getComputedStyle(document.documentElement).getPropertyValue("--chart-text").trim() || "#dbeafe";
}

function chartGridColor() {
  return getComputedStyle(document.documentElement).getPropertyValue("--chart-grid").trim() || "rgba(148, 163, 184, 0.16)";
}

function configureChartTheme() {
  if (!window.Chart) return;
  window.Chart.defaults.color = chartTextColor();
  window.Chart.defaults.font.family = '"DIN 2014", "BIZ UDPGothic", "Noto Sans JP", sans-serif';
  window.Chart.defaults.plugins.legend.labels.usePointStyle = true;
}

const doughnutPercentLabels = {
  id: "doughnutPercentLabels",
  afterDatasetsDraw(chart) {
    if (chart.config.type !== "doughnut" && chart.config.type !== "pie") return;
    const dataset = chart.data?.datasets?.[0];
    const arcs = chart.getDatasetMeta(0)?.data || [];
    const values = (dataset?.data || []).map((value) => Math.max(0, Number(value || 0)));
    const total = values.reduce((sum, value) => sum + value, 0);
    if (!total) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "rgba(5, 10, 20, 0.62)";
    ctx.lineWidth = 3;
    ctx.font = '800 11px "DIN 2014", "BIZ UDPGothic", sans-serif';
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    arcs.forEach((arc, index) => {
      const value = values[index] || 0;
      if (!value) return;
      const pct = (value / total) * 100;
      if (pct < 3.5) return;
      const props = arc.getProps(["x", "y", "startAngle", "endAngle", "innerRadius", "outerRadius"], true);
      const angle = (props.startAngle + props.endAngle) / 2;
      const radius = props.innerRadius + (props.outerRadius - props.innerRadius) * 0.62;
      const x = props.x + Math.cos(angle) * radius;
      const y = props.y + Math.sin(angle) * radius;
      const text = `${pct.toFixed(1)}%`;
      ctx.strokeText(text, x, y);
      ctx.fillText(text, x, y);
    });
    ctx.restore();
  },
};

function lineChart(id, labels, datasets) {
  createChart(id, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      plugins: { legend: { position: "bottom" } },
      scales: {
        x: { grid: { color: chartGridColor() } },
        y: { beginAtZero: true, grid: { color: chartGridColor() } },
      },
    },
  });
}

function barLineChart(id, labels, barData, lineData, options = {}) {
  const barLabel = options.barLabel || "アクティブユーザー数";
  const lineLabel = options.lineLabel || "メッセージ数";
  const leftAxisLabel = options.leftAxisLabel || "ユーザー数";
  const rightAxisLabel = options.rightAxisLabel || lineLabel;
  createChart(id, {
    data: {
      labels,
      datasets: [
        {
          type: "bar",
          label: barLabel,
          data: barData,
          backgroundColor: "rgba(37, 99, 235, 0.72)",
          borderRadius: 8,
          yAxisID: "y",
        },
        {
          type: "line",
          label: lineLabel,
          data: lineData,
          borderColor: COLORS.teal,
          backgroundColor: "rgba(14, 165, 164, 0.16)",
          tension: 0.36,
          pointRadius: 3,
          yAxisID: "y1",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      plugins: { legend: { position: "bottom" } },
      scales: {
        y: { beginAtZero: true, position: "left", grid: { color: chartGridColor() }, title: { display: true, text: leftAxisLabel } },
        y1: {
          beginAtZero: true,
          position: "right",
          grid: { drawOnChartArea: false },
          title: { display: true, text: rightAxisLabel },
        },
      },
    },
  });
}

function doughnutChart(id, rows, centerText = "") {
  const labels = rows.map((row) => row.label);
  const values = rows.map((row) => row.count);
  const centerPlugin = {
    id: `${id}-center`,
    afterDraw(chart) {
      if (!centerText) return;
      const meta = chart.getDatasetMeta(0);
      const arc = meta?.data?.[0];
      if (!arc) return;
      const { x, y } = arc;
      const ctx = chart.ctx;
      const centerLines = Array.isArray(centerText) ? centerText : [centerText];
      ctx.save();
      ctx.textAlign = "center";
      ctx.fillStyle = chartTextColor();
      ctx.font = '800 11px "BIZ UDPGothic", "Noto Sans JP", sans-serif';
      if (centerLines.length > 1) {
        ctx.fillStyle = "rgba(220, 232, 255, 0.74)";
        ctx.fillText(centerLines[0], x, y - 8);
        ctx.fillStyle = chartTextColor();
        ctx.font = '900 18px "DIN 2014", "Noto Sans JP", sans-serif';
        ctx.fillText(centerLines[1], x, y + 14);
      } else {
        ctx.font = '900 16px "DIN 2014", "Noto Sans JP", sans-serif';
        ctx.fillText(centerLines[0], x, y + 4);
      }
      ctx.restore();
    },
  };
  createChart(id, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{ data: values, backgroundColor: chartColors(values.length), borderWidth: 2, borderColor: "#fff" }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom" },
        tooltip: {
          callbacks: {
            label(ctx) {
              const row = rows[ctx.dataIndex] || {};
              return `${row.label}: ${displayCount(row.count)}件 (${displayRate(row.rate)})`;
            },
          },
        },
      },
      cutout: "62%",
    },
    plugins: [centerPlugin, doughnutPercentLabels],
  });
}

function horizontalBarChart(id, rows) {
  createChart(id, {
    type: "bar",
    data: {
      labels: rows.map((row) => row.label),
      datasets: [
        {
          label: "件数",
          data: rows.map((row) => row.count),
          backgroundColor: chartColors(rows.length),
          borderRadius: 8,
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { beginAtZero: true, grid: { color: chartGridColor() } },
        y: { grid: { display: false } },
      },
    },
  });
}

function renderKpis(viewModel) {
  const grid = $("kpiCardsPrimary");
  if (!grid) return;
  grid.innerHTML = viewModel.kpis
    .map((card) => {
      const badge = card.statusBadge
        ? `<span class="statusBadge ${card.statusBadge.tone}">${escapeHtml(card.statusBadge.label)}</span>`
        : "";
      return `
        <article class="kpiCard ${card.tone || "neutral"}">
          <div class="kpiLabel">
            <span>${escapeHtml(card.label)}</span>
            <span class="helpWrap">
              <button type="button" class="helpBtn" aria-label="${escapeHtml(card.label)}の説明">?</button>
              <span class="helpTooltip">${escapeHtml(card.help)}</span>
            </span>
          </div>
          <div class="kpiValue">${escapeHtml(card.value)}</div>
          ${badge}
        </article>
      `;
    })
    .join("");
  $("summaryWindowLabel").textContent = viewModel.windowLabel;
  $("environmentWindowLabel").textContent = viewModel.windowLabel;
  $("qualityWindowLabel").textContent = viewModel.windowLabel;
  $("followupWindowLabel").textContent = viewModel.windowLabel;
  $("dataFreshness").textContent = `最終更新: ${displayDateTime(viewModel.generatedAt)}${viewModel.fetchMs !== undefined ? ` / API ${viewModel.fetchMs} ms` : ""}`;
}

function renderUsageTrend(viewModel) {
  const rows = viewModel.usageTrend;
  barLineChart(
    "usageTrendChart",
    rows.map((row) => row.label),
    rows.map((row) => row.activeUserCount),
    rows.map((row) => row.messageCount),
  );
}

function renderSystemUsageChart(viewModel) {
  renderUsageTrend(viewModel);
}

function renderActivity(viewModel) {
  const rows = viewModel.activityDistribution.segments;
  doughnutChart("activityChart", rows, ["総ユーザー数", displayCount(viewModel.activityDistribution.totalUserCount)]);
  const legend = $("activityLegend");
  if (!legend) return;
  legend.innerHTML = rows
    .map(
      (row) => `
        <div class="legendItem">
          <strong>${escapeHtml(row.label)}</strong>
          <span>${displayRate(row.rate, 2)}（${displayCount(row.count)}）</span>
          <small>${escapeHtml(row.definition || "")}</small>
        </div>
      `,
    )
    .join("");
}

function renderEnvironment(viewModel) {
  const requestRows = viewModel.environmentMode.requestByHour;
  lineChart("requestByHourChart", requestRows.map((row) => row.label), [
    {
      label: "リクエスト数",
      data: requestRows.map((row) => row.count),
      borderColor: COLORS.blue,
      backgroundColor: "rgba(37, 99, 235, 0.14)",
      fill: true,
      tension: 0.34,
    },
  ]);
  doughnutChart("deviceChart", viewModel.environmentMode.deviceDistribution);
  doughnutChart("modeChart", viewModel.environmentMode.modeDistribution);
}

function renderAnswerQuality(viewModel) {
  const grid = $("answerQualityGrid");
  if (!grid) return;
  grid.innerHTML = viewModel.answerQuality
    .map(
      (metric) => `
        <article class="qualityCard">
          <h3>${escapeHtml(metric.title)}</h3>
          <div class="qualityRows">
            ${
              metric.rows.length
                ? metric.rows
                    .map(
                      (row) => `
                        <div class="qualityRow">
                          <span>${escapeHtml(row.label)}</span>
                          <strong>${displayCount(row.count)}</strong>
                          <em>${displayRate(row.rate)}</em>
                        </div>
                      `,
                    )
                    .join("")
                : `<div class="emptyInline">対象データなし</div>`
            }
          </div>
        </article>
      `,
    )
    .join("");
}

function renderFollowup(viewModel) {
  const cards = $("followupCards");
  if (cards) {
    cards.innerHTML = viewModel.followup.cards
      .map((card) => `<article class="miniKpi"><span>${escapeHtml(card.label)}</span><strong>${escapeHtml(card.value)}</strong></article>`)
      .join("");
  }
  horizontalBarChart("followupFunnelChart", viewModel.followup.funnel);
}

function openExportDialog(scope = "global") {
  const isUser = scope === "user";
  $("globalExportData")?.classList.toggle("hidden", isUser);
  $("userExportData")?.classList.toggle("hidden", !isUser);
  $("exportDialog")?.showModal();
}

async function loadDashboard() {
  setStatus("ダッシュボード取得中", "loading");
  const results = await Promise.allSettled([
    getSystemDashboard(timeRangeQuery(state.dashboardPreset), { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS }),
    getSystemDashboard(timeRangeQuery(state.usagePreset), { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS }),
    getSystemDashboard(timeRangeQuery(state.activityPreset), { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS }),
  ]);
  const [mainResult, usageResult, activityResult] = results;
  if (mainResult.status === "rejected" && usageResult.status === "rejected" && activityResult.status === "rejected") {
    throw mainResult.reason;
  }

  let mainViewModel = null;
  if (mainResult.status === "fulfilled") {
    mainViewModel = toDashboardViewModel(mainResult.value, state.dashboardPreset);
    renderKpis(mainViewModel);
    renderEnvironment(mainViewModel);
    renderAnswerQuality(mainViewModel);
    renderFollowup(mainViewModel);
  }

  if (usageResult.status === "fulfilled") {
    renderSystemUsageChart(toDashboardViewModel(usageResult.value, state.usagePreset));
  } else if (mainViewModel) {
    renderSystemUsageChart(mainViewModel);
  }

  if (activityResult.status === "fulfilled") {
    renderActivity(toDashboardViewModel(activityResult.value, state.activityPreset));
  } else if (mainViewModel) {
    renderActivity(mainViewModel);
  }

  setStatus(results.every((result) => result.status === "fulfilled") ? "表示中" : "一部表示中", mainResult.status === "fulfilled" ? "success" : "error");
}

function renderUsers(rows) {
  const tbody = $("usersTable")?.querySelector("tbody");
  if (!tbody) return;
  if (!rows.length) {
    state.userRows = [];
    tbody.innerHTML = `<tr><td colspan="9" class="emptyCell">対象ユーザーがありません。</td></tr>`;
    $("usersPageStatus").textContent = "0件 / 1ページ目";
    $("prevUsersPage").disabled = true;
    $("nextUsersPage").disabled = true;
    return;
  }
  const filteredRows = rows.filter((row) => {
    const id = String(row.userId || "").toLowerCase();
    const email = String(row.userEmail || "").toLowerCase();
    return id && id !== "unknown" && !id.includes("lcs-agent") && !email.includes("lcs-agent");
  });
  state.userRows = filteredRows;
  const maxPage = Math.max(1, Math.ceil(filteredRows.length / USERS_PAGE_SIZE));
  if (state.usersPage > maxPage) state.usersPage = maxPage;
  const start = (state.usersPage - 1) * USERS_PAGE_SIZE;
  const pageRows = filteredRows.slice(start, start + USERS_PAGE_SIZE);
  if (!pageRows.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="emptyCell">対象ユーザーがありません。</td></tr>`;
  } else {
    tbody.innerHTML = pageRows
      .map(
        (row) => `
          <tr>
            <td><button class="copyBtn" data-copy="${escapeHtml(row.userId)}">${escapeHtml(truncateMiddle(row.userId, 12, 8))}</button></td>
            <td>${escapeHtml(row.userEmail || "-")}</td>
            <td>${escapeHtml(row.lastActiveAtJst)}</td>
            <td class="numericCell">${escapeHtml(row.activeDays7)}</td>
            <td class="numericCell">${escapeHtml(row.messageCount7d)}</td>
            <td class="numericCell">${escapeHtml(row.coverageRate)}</td>
            <td class="numericCell">${escapeHtml(row.badFeedbackRate)}</td>
            <td><span class="activityBadge ${escapeHtml(row.activityKey)}">${escapeHtml(row.activityLevel)}</span></td>
            <td><a class="detailBtn" href="/dashboard?user_id=${encodeURIComponent(row.userId)}" data-user-id="${escapeHtml(row.userId)}">詳細</a></td>
          </tr>
        `,
      )
      .join("");
  }
  $("usersPageStatus").textContent = `${displayCount(filteredRows.length)}件 / ${state.usersPage}ページ目`;
  $("prevUsersPage").disabled = state.usersPage <= 1;
  $("nextUsersPage").disabled = state.usersPage >= maxPage;
}

async function loadUsersTable() {
  const payload = await getUsers({
    ...timeRangeQuery("last_7d"),
    activity: state.userFilter,
    q: state.userQuery,
    limit: 1000,
  });
  renderUsers(toUserRows(payload));
}

function renderMiniCards(containerId, cards) {
  const el = $(containerId);
  if (!el) return;
  el.innerHTML = cards.map((card) => `<article class="miniKpi"><span>${escapeHtml(card.label)}</span><strong>${escapeHtml(card.value)}</strong></article>`).join("");
}

function renderUserDetail(viewModel) {
  $("dashboardView")?.classList.add("hidden");
  $("userDetailView")?.classList.remove("hidden");
  $("userDetailTitle").textContent = viewModel.user.title;
  renderMiniCards("userSummaryCards", viewModel.summaryCards);
  barLineChart(
    "userTrendChart",
    viewModel.trend.map((row) => row.label),
    viewModel.trend.map((row) => row.messageCount),
    viewModel.trend.map((row) => Math.round(Number(row.answerSuccessRate || 0) * 100)),
    {
      barLabel: "メッセージ数",
      lineLabel: "回答成功率（%）",
      leftAxisLabel: "メッセージ数",
      rightAxisLabel: "回答成功率（%）",
    },
  );
  doughnutChart("userModeChart", viewModel.modeDistribution);

  const tbody = $("conversationTable")?.querySelector("tbody");
  if (!tbody) return;
  if (!viewModel.conversations.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="emptyCell">会話データがありません。</td></tr>`;
    return;
  }
  tbody.innerHTML = viewModel.conversations
    .map(
      (row) => `
        <tr class="conversationRow" data-conversation-id="${escapeHtml(row.conversationId)}">
          <td><button class="copyBtn" data-copy="${escapeHtml(row.conversationId)}">${escapeHtml(truncateMiddle(row.conversationId, 9, 5))}</button></td>
          <td class="ellipsisCell" title="${escapeHtml(row.title)}">${escapeHtml(row.titleShort || row.title)}</td>
          <td class="numericCell">${escapeHtml(row.messageCount)}</td>
          <td>${escapeHtml(row.updatedAtJst)}</td>
        </tr>
      `,
    )
    .join("");
}

async function openUserDetail(userId) {
  state.selectedUserId = userId;
  state.selectedConversationId = "";
  state.messageCursor = "";
  state.includeMessageContent = false;
  setStatus("ユーザー詳細取得中", "loading");
  const payload = await getUserDetail(userId, {
    ...timeRangeQuery(state.dashboardPreset),
    conversation_limit: 200,
    include_hidden: false,
    include_messages: false,
  });
  renderUserDetail(toUserDetailViewModel(payload));
  $("userDetailView")?.scrollIntoView({ behavior: "smooth", block: "start" });
  setStatus("表示中", "success");
}

function renderMessages(rows, append = false) {
  const tbody = $("messagesTable")?.querySelector("tbody");
  if (!tbody) return;
  const html = rows
    .map(
      (row) => `
        <tr>
          <td>${escapeHtml(row.timestamp)}</td>
          <td>${escapeHtml(row.role)}</td>
          <td class="contentCell">${escapeHtml(row.contentPreview)}</td>
          <td>${escapeHtml(row.mode)}</td>
          <td>${escapeHtml(row.device)}</td>
          <td>${escapeHtml(row.coverageRate)}</td>
          <td>${escapeHtml(row.feedback)}</td>
        </tr>
      `,
    )
    .join("");
  if (append) {
    tbody.insertAdjacentHTML("beforeend", html);
  } else {
    tbody.innerHTML = html || `<tr><td colspan="7" class="emptyCell">対象メッセージがありません。</td></tr>`;
  }
}

async function loadMessages({ append = false, includeContent = state.includeMessageContent } = {}) {
  if (!state.selectedUserId || !state.selectedConversationId) return;
  setStatus("メッセージ取得中", "loading");
  const payload = await getTraceMessages({
    ...timeRangeQuery(state.dashboardPreset),
    user_id: state.selectedUserId,
    conversation_id: state.selectedConversationId,
    limit: 500,
    cursor: append ? state.messageCursor : "",
    include_content: includeContent,
  });
  state.messageCursor = payload?.page?.nextCursor || "";
  $("messagePanelTitle").textContent = includeContent
    ? "本文を表示しています。取り扱いに注意してください。"
    : "プレビューのみ表示しています。";
  renderMessages(toMessageRows(payload), append);
  setStatus("表示中", "success");
}

async function copyText(value) {
  const text = String(value || "");
  if (!text) return;
  await navigator.clipboard.writeText(text);
  toast("コピーしました");
}

function bindEvents() {
  $("dashboardPreset")?.addEventListener("change", async (event) => {
    state.dashboardPreset = event.target.value;
    await reloadCurrentView();
  });
  $("usagePreset")?.addEventListener("change", async (event) => {
    state.usagePreset = event.target.value;
    await loadDashboard();
  });
  $("activityPreset")?.addEventListener("change", async (event) => {
    state.activityPreset = event.target.value;
    await loadDashboard();
  });
  $("refreshAll")?.addEventListener("click", reloadCurrentView);
  $("activityFilter")?.addEventListener("change", async (event) => {
    state.userFilter = event.target.value;
    state.usersPage = 1;
    await loadUsersTable();
  });
  $("loadUsers")?.addEventListener("click", async () => {
    state.userQuery = $("userSearch")?.value || "";
    state.usersPage = 1;
    await loadUsersTable();
  });
  $("userSearch")?.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      state.userQuery = event.target.value || "";
      state.usersPage = 1;
      await loadUsersTable();
    }
  });
  $("prevUsersPage")?.addEventListener("click", () => {
    state.usersPage = Math.max(1, state.usersPage - 1);
    renderUsers(state.userRows);
  });
  $("nextUsersPage")?.addEventListener("click", () => {
    state.usersPage += 1;
    renderUsers(state.userRows);
  });
  $("backToDashboard")?.addEventListener("click", () => {
    window.location.href = "/dashboard";
  });
  $("openExportDialog")?.addEventListener("click", () => openExportDialog("global"));
  $("exportUserDetail")?.addEventListener("click", () => openExportDialog("user"));
  $("confirmExport")?.addEventListener("click", () => {
    if ($("exportIncludeContent")?.checked) {
      const ok = window.confirm("メッセージ本文を含む出力には個人情報や業務情報が含まれる可能性があります。続行しますか？");
      if (!ok) return;
    }
    toast("export/jobs 接続は後続実装です。設定内容は UI 側で保持できます。");
  });

  document.addEventListener("click", async (event) => {
    const copyBtn = event.target.closest("[data-copy]");
    if (copyBtn) {
      await copyText(copyBtn.dataset.copy);
      return;
    }
    const detailBtn = event.target.closest("[data-user-id]");
    if (detailBtn) {
      if (detailBtn.tagName === "A") return;
      window.location.href = `/dashboard?user_id=${encodeURIComponent(detailBtn.dataset.userId)}`;
      return;
    }
    const messageBtn = event.target.closest("[data-conversation-id]");
    if (messageBtn) {
      state.selectedConversationId = messageBtn.dataset.conversationId;
      document.querySelectorAll(".conversationRow.active").forEach((row) => row.classList.remove("active"));
      messageBtn.classList.add("active");
      state.messageCursor = "";
      state.includeMessageContent = false;
      await loadMessages({ includeContent: false });
    }
  });
}

async function loadAll() {
  setStatus("取得中", "loading");
  const results = await Promise.allSettled([loadDashboard(), loadUsersTable()]);
  const failed = results.filter((result) => result.status === "rejected");
  if (failed.length) {
    failed.forEach((result) => console.error(result.reason));
    setStatus("一部データの取得に失敗しました", "error");
    toast("一部データの取得に失敗しました。表示可能な項目のみ表示しています。");
  }
}

function startApp() {
  configureChartTheme();
  bindEvents();
  const userId = currentUserDetailId();
  if (userId) {
    $("dashboardView")?.classList.add("hidden");
    $("userDetailView")?.classList.remove("hidden");
    openUserDetail(userId).catch((error) => {
      console.error(error);
      setStatus("ユーザー詳細の取得に失敗しました", "error");
    });
  } else {
    loadAll();
  }
}

if (document.readyState === "complete") {
  startApp();
} else {
  window.addEventListener("load", startApp, { once: true });
}
