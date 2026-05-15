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
  userFilter: "",
  userQuery: "",
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
  const palette = [COLORS.blue, COLORS.teal, COLORS.amber, COLORS.violet, COLORS.cyan, COLORS.slate, COLORS.red];
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
      ctx.save();
      ctx.textAlign = "center";
      ctx.fillStyle = chartTextColor();
      ctx.font = '700 18px "Noto Sans JP", sans-serif';
      ctx.fillText(centerText, x, y + 4);
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
    plugins: [centerPlugin],
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
            <button type="button" class="helpBtn" data-help-title="${escapeHtml(card.label)}" data-help-body="${escapeHtml(card.help)}" aria-label="${escapeHtml(card.label)}の説明">?</button>
          </div>
          <div class="kpiValue">${escapeHtml(card.value)}</div>
          ${badge}
        </article>
      `;
    })
    .join("");
  $("summaryWindowLabel").textContent = viewModel.windowLabel;
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
  doughnutChart("activityChart", rows, displayCount(viewModel.activityDistribution.totalUserCount));
  const legend = $("activityLegend");
  if (!legend) return;
  legend.innerHTML = rows
    .map(
      (row) => `
        <div class="legendItem">
          <strong>${escapeHtml(row.label)}</strong>
          <span>${displayCount(row.count)}人 / ${displayRate(row.rate)}${row.definition ? ` / ${escapeHtml(row.definition)}` : ""}</span>
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

function openHelpDialog(title, body) {
  $("helpDialogTitle").textContent = title || "指標の説明";
  $("helpDialogBody").textContent = body || "-";
  $("helpDialog")?.showModal();
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
  ]);
  const [mainResult, usageResult] = results;
  if (mainResult.status === "rejected" && usageResult.status === "rejected") {
    throw mainResult.reason;
  }

  let mainViewModel = null;
  if (mainResult.status === "fulfilled") {
    mainViewModel = toDashboardViewModel(mainResult.value, state.dashboardPreset);
    renderKpis(mainViewModel);
    renderActivity(mainViewModel);
    renderEnvironment(mainViewModel);
    renderAnswerQuality(mainViewModel);
    renderFollowup(mainViewModel);
  }

  if (usageResult.status === "fulfilled") {
    renderSystemUsageChart(toDashboardViewModel(usageResult.value, state.usagePreset));
  } else if (mainViewModel) {
    renderSystemUsageChart(mainViewModel);
  }

  setStatus(mainResult.status === "fulfilled" && usageResult.status === "fulfilled" ? "表示中" : "一部表示中", mainResult.status === "fulfilled" ? "success" : "error");
}

function renderUsers(rows) {
  const tbody = $("usersTable")?.querySelector("tbody");
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="emptyCell">対象ユーザーがありません。</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td><button class="copyBtn" data-copy="${escapeHtml(row.userId)}">${escapeHtml(truncateMiddle(row.userId, 12, 8))}</button></td>
          <td>${escapeHtml(row.userEmail || "-")}</td>
          <td>${escapeHtml(row.lastActiveAtJst)}</td>
          <td>${escapeHtml(row.activeDays7)}</td>
          <td>${escapeHtml(row.messageCount7d)}</td>
          <td>${escapeHtml(row.coverageRate)}</td>
          <td>${escapeHtml(row.badFeedbackRate)}</td>
          <td><span class="activityBadge ${escapeHtml(row.activityKey)}">${escapeHtml(row.activityLevel)}</span></td>
          <td><button class="detailBtn" data-user-id="${escapeHtml(row.userId)}">詳細</button></td>
        </tr>
      `,
    )
    .join("");
}

async function loadUsersTable() {
  const payload = await getUsers({
    ...timeRangeQuery("last_7d"),
    activity: state.userFilter,
    q: state.userQuery,
    limit: 100,
  });
  renderUsers(toUserRows(payload));
}

function renderMiniCards(containerId, cards) {
  const el = $(containerId);
  if (!el) return;
  el.innerHTML = cards.map((card) => `<article class="miniKpi"><span>${escapeHtml(card.label)}</span><strong>${escapeHtml(card.value)}</strong></article>`).join("");
}

function renderUserDetail(viewModel) {
  $("userDetailPanel")?.classList.remove("hidden");
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
    tbody.innerHTML = `<tr><td colspan="11" class="emptyCell">会話データがありません。</td></tr>`;
    return;
  }
  tbody.innerHTML = viewModel.conversations
    .map(
      (row) => `
        <tr>
          <td><button class="copyBtn" data-copy="${escapeHtml(row.conversationId)}">${escapeHtml(truncateMiddle(row.conversationId, 14, 8))}</button></td>
          <td>${escapeHtml(row.title)}</td>
          <td>${escapeHtml(row.mode)}</td>
          <td>${escapeHtml(row.visibility)}</td>
          <td>${escapeHtml(row.createdAtJst)}</td>
          <td>${escapeHtml(row.updatedAtJst)}</td>
          <td>${escapeHtml(row.messageCount)}</td>
          <td>${escapeHtml(row.integrityState)}</td>
          <td>${escapeHtml(row.isFavorite)}</td>
          <td>${escapeHtml(row.followupRuntimeSummary)}</td>
          <td><button class="messageBtn" data-conversation-id="${escapeHtml(row.conversationId)}">確認</button></td>
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
    conversation_limit: 50,
    include_hidden: false,
    include_messages: false,
  });
  renderUserDetail(toUserDetailViewModel(payload));
  $("userDetailPanel")?.scrollIntoView({ behavior: "smooth", block: "start" });
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
          <td>${escapeHtml(row.status)}</td>
          <td>${escapeHtml(row.mode)}</td>
          <td>${escapeHtml(row.device)}</td>
          <td>${escapeHtml(row.feedback)}</td>
          <td class="contentCell">${escapeHtml(row.contentPreview)}</td>
          <td><button class="copyBtn" data-copy="${escapeHtml(row.traceId)}">${escapeHtml(row.traceShort)}</button></td>
          <td><button class="copyBtn" data-copy="${escapeHtml(row.requestId)}">${escapeHtml(row.requestShort)}</button></td>
          <td><button class="copyBtn" data-copy="${escapeHtml(row.turnId)}">${escapeHtml(row.turnShort)}</button></td>
          <td><button class="copyBtn" data-copy="${escapeHtml(row.messageId)}">${escapeHtml(row.messageShort)}</button></td>
        </tr>
      `,
    )
    .join("");
  if (append) {
    tbody.insertAdjacentHTML("beforeend", html);
  } else {
    tbody.innerHTML = html || `<tr><td colspan="11" class="emptyCell">対象メッセージがありません。</td></tr>`;
  }
}

async function loadMessages({ append = false, includeContent = state.includeMessageContent } = {}) {
  if (!state.selectedUserId || !state.selectedConversationId) return;
  setStatus("メッセージ取得中", "loading");
  const payload = await getTraceMessages({
    ...timeRangeQuery(state.dashboardPreset),
    user_id: state.selectedUserId,
    conversation_id: state.selectedConversationId,
    limit: 100,
    cursor: append ? state.messageCursor : "",
    include_content: includeContent,
  });
  state.messageCursor = payload?.page?.nextCursor || "";
  $("loadMoreMessages").disabled = !state.messageCursor;
  $("messagePanel")?.classList.remove("hidden");
  $("messagePanelTitle").textContent = includeContent
    ? "本文を表示しています。取り扱いに注意してください。"
    : "プレビューのみ表示しています。本文は取得していません。";
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
    await loadAll();
  });
  $("usagePreset")?.addEventListener("change", async (event) => {
    state.usagePreset = event.target.value;
    await loadDashboard();
  });
  $("refreshAll")?.addEventListener("click", loadAll);
  $("activityFilter")?.addEventListener("change", async (event) => {
    state.userFilter = event.target.value;
    await loadUsersTable();
  });
  $("loadUsers")?.addEventListener("click", async () => {
    state.userQuery = $("userSearch")?.value || "";
    await loadUsersTable();
  });
  $("userSearch")?.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      state.userQuery = event.target.value || "";
      await loadUsersTable();
    }
  });
  $("closeUserDetail")?.addEventListener("click", () => {
    $("userDetailPanel")?.classList.add("hidden");
  });
  $("showMessageContent")?.addEventListener("click", async () => {
    const ok = window.confirm("メッセージ本文には個人情報や業務情報が含まれる可能性があります。本文を表示しますか？");
    if (!ok) return;
    state.includeMessageContent = true;
    state.messageCursor = "";
    await loadMessages({ includeContent: true });
  });
  $("loadMoreMessages")?.addEventListener("click", () => loadMessages({ append: true }));
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
    const helpBtn = event.target.closest("[data-help-title]");
    if (helpBtn) {
      openHelpDialog(helpBtn.dataset.helpTitle, helpBtn.dataset.helpBody);
      return;
    }
    const detailBtn = event.target.closest("[data-user-id]");
    if (detailBtn) {
      await openUserDetail(detailBtn.dataset.userId);
      return;
    }
    const messageBtn = event.target.closest("[data-conversation-id]");
    if (messageBtn) {
      state.selectedConversationId = messageBtn.dataset.conversationId;
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
  loadAll();
}

if (document.readyState === "complete") {
  startApp();
} else {
  window.addEventListener("load", startApp, { once: true });
}
