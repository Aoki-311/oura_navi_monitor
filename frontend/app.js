import { createExportJob, deleteExportJob, downloadExportJob, isCancellation } from "./api/client.js";
import { exportJobModel } from "./adapters/exportAdapter.js";
import { destroyAllCharts } from "./components/charts.js";
import { OverviewPage } from "./pages/overviewPage.js";
import { bindDateRangeControl, normalizeDateRange, renderDateRangeControl, requestDateRange } from "./components/dateRangeControl.js";
import { moduleMessage, setBusy } from "./components/dom.js";
import { UserAnalysisPage } from "./pages/userAnalysisPage.js";
import { UserManagementPage } from "./pages/userManagementPage.js";

const root = document.querySelector("#pageRoot");
const dateRangeHost = document.querySelector("#mainDateRange");
let mainDateControl = null;
let mainDateControlPage = "";
const exportButton = document.querySelector("#exportButton");
const toastElement = document.querySelector("#toast");
const validPresets = new Set(["today", "last_7d", "last_14d", "last_30d", "last_60d", "all", "custom"]);
const validActivities = new Set(["", "high", "middle", "low", "dormant"]);
const validManagementStatuses = new Set(["all", "active", "inactive"]);
const validOverviewSorts = new Set(["last_desc", "name_asc", "messages_desc", "success_desc"]);
const validManagementSorts = new Set(["name_asc", "updated_desc", "area_asc"]);
let toastTimer = 0;
let renderGeneration = 0;
let renderController = null;
let activePage = null;
let exportTransaction = null;
let exportRetry = null;
const exportCleanupJobs = new Set();
let analyticsSnapshot = null;
let pageLeaveGuard = null;
const HISTORY_INDEX_KEY = "monitorHistoryIndex";
let historyIndex = Number.isInteger(window.history.state?.[HISTORY_INDEX_KEY])
  ? window.history.state[HISTORY_INDEX_KEY]
  : 0;
let renderedHistoryIndex = historyIndex;
let renderedUrl = window.location.href;
let restoringHistory = false;
if (!Number.isInteger(window.history.state?.[HISTORY_INDEX_KEY])) {
  window.history.replaceState(
    { ...(window.history.state || {}), [HISTORY_INDEX_KEY]: historyIndex },
    "",
    window.location.href,
  );
}

function stateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const page = ["overview", "user", "management"].includes(params.get("page")) ? params.get("page") : "overview";
  const selectedPreset = validPresets.has(params.get("preset")) ? params.get("preset") : "last_7d";
  const mainRange = normalizeDateRange({ preset: selectedPreset, start: params.get("start"), end: params.get("end") });
  const moduleRange = (prefix) => ["preset", "start", "end"].some((key) => params.has(`${prefix}_${key}`))
    ? normalizeDateRange({ preset: params.get(`${prefix}_preset`), start: params.get(`${prefix}_start`), end: params.get(`${prefix}_end`) })
    : { ...mainRange };
  const pageNumber = (key) => Math.max(1, Number.parseInt(params.get(key) || "1", 10) || 1);
  return {
    page,
    roster: page === "user" || page === "management" ? (params.get("roster") || "") : "",
    area: page === "overview" ? (params.get("area") || "") : "",
    ...mainRange,
    environmentRange: moduleRange("env"),
    trendRange: moduleRange("trend"),
    overviewQuery: params.get("overview_q") || "",
    overviewActivity: validActivities.has(params.get("overview_activity") || "") ? (params.get("overview_activity") || "") : "",
    overviewSort: validOverviewSorts.has(params.get("overview_sort") || "last_desc") ? (params.get("overview_sort") || "last_desc") : "last_desc",
    overviewPage: pageNumber("overview_page"),
    userQuery: params.get("user_q") || "",
    userPage: pageNumber("user_page"),
    managementQuery: params.get("management_q") || "",
    managementStatus: validManagementStatuses.has(params.get("management_status") || "all") ? (params.get("management_status") || "all") : "all",
    managementRole: params.get("management_role") || "",
    managementDepartment: params.get("management_department") || "",
    managementLabel: params.get("management_label") || "",
    managementSort: validManagementSorts.has(params.get("management_sort") || "name_asc") ? (params.get("management_sort") || "name_asc") : "name_asc",
    managementPage: pageNumber("management_page"),
    managementSubtab: params.get("management_tab") === "labels" ? "labels" : "users",
  };
}

function dashboardUrl(state) {
  const params = new URLSearchParams();
  if (state.page !== "overview") params.set("page", state.page);
  if (state.roster && (state.page === "user" || state.page === "management")) params.set("roster", state.roster);
  if (state.area && state.page === "overview") params.set("area", state.area);
  if (state.preset !== "last_7d") params.set("preset", state.preset);
  if (state.start) params.set("start", state.start);
  if (state.end) params.set("end", state.end);
  for (const [prefix, range] of [["env", state.environmentRange], ["trend", state.trendRange]]) {
    if (!range) continue;
    if (range.preset !== "last_7d") params.set(`${prefix}_preset`, range.preset);
    if (range.start) params.set(`${prefix}_start`, range.start);
    if (range.end) params.set(`${prefix}_end`, range.end);
  }
  const optional = {
    overview_q: state.overviewQuery,
    overview_activity: state.overviewActivity,
    overview_sort: state.overviewSort !== "last_desc" ? state.overviewSort : "",
    overview_page: state.overviewPage > 1 ? state.overviewPage : "",
    user_q: state.userQuery,
    user_page: state.userPage > 1 ? state.userPage : "",
    management_q: state.managementQuery,
    management_status: state.managementStatus !== "all" ? state.managementStatus : "",
    management_role: state.managementRole,
    management_department: state.managementDepartment,
    management_label: state.managementLabel,
    management_sort: state.managementSort !== "name_asc" ? state.managementSort : "",
    management_page: state.managementPage > 1 ? state.managementPage : "",
    management_tab: state.managementSubtab !== "users" ? state.managementSubtab : "",
  };
  Object.entries(optional).forEach(([key, value]) => { if (value !== "" && value != null) params.set(key, String(value)); });
  const query = params.toString();
  return `/dashboard${query ? `?${query}` : ""}`;
}

function writeState(state, { replace = false } = {}) {
  if (replace) {
    window.history.replaceState(
      { ...(window.history.state || {}), [HISTORY_INDEX_KEY]: historyIndex },
      "",
      dashboardUrl(state),
    );
    renderedUrl = dashboardUrl(state);
    return;
  }
  historyIndex += 1;
  window.history.pushState({ [HISTORY_INDEX_KEY]: historyIndex }, "", dashboardUrl(state));
}

function navigate(page, values = {}, options = {}) {
  const current = stateFromUrl();
  const next = {
    ...current,
    page,
    preset: values.preset ?? current.preset,
    start: values.start ?? current.start,
    end: values.end ?? current.end,
    environmentRange: values.environmentRange ?? current.environmentRange,
    trendRange: values.trendRange ?? current.trendRange,
    roster: values.roster ?? ((page === current.page && (page === "user" || page === "management")) ? current.roster : ""),
    area: values.area ?? ((page === current.page && page === "overview") ? current.area : ""),
    overviewQuery: values.overviewQuery ?? current.overviewQuery,
    overviewActivity: values.overviewActivity ?? current.overviewActivity,
    overviewSort: values.overviewSort ?? current.overviewSort,
    overviewPage: values.overviewPage ?? current.overviewPage,
    userQuery: values.userQuery ?? current.userQuery,
    userPage: values.userPage ?? current.userPage,
    managementQuery: values.managementQuery ?? current.managementQuery,
    managementStatus: values.managementStatus ?? current.managementStatus,
    managementRole: values.managementRole ?? current.managementRole,
    managementDepartment: values.managementDepartment ?? current.managementDepartment,
    managementLabel: values.managementLabel ?? current.managementLabel,
    managementSort: values.managementSort ?? current.managementSort,
    managementPage: values.managementPage ?? current.managementPage,
    managementSubtab: values.managementSubtab ?? current.managementSubtab,
  };
  if (options.render !== false && !canLeaveCurrentPage()) return false;
  if (exportRouteKey(current) !== exportRouteKey(next)) invalidateExportContext();
  const canStageSamePage = options.render !== false
    && page === current.page
    && (page === "overview" || page === "user")
    && activePage?.name === page
    && activePage.controller === renderController
    && !activePage.controller.signal.aborted;
  if (canStageSamePage) {
    void render({
      focusMain: true,
      requestedState: next,
      navigation: { state: next, options },
    });
    return true;
  }
  writeState(next, options);
  if (options.render !== false) void render({ focusMain: true });
  return true;
}

function clearManagementRoster() {
  const state = stateFromUrl();
  if (state.page === "management" && state.roster) writeState({ ...state, roster: "" }, { replace: true });
}

function toast(message, type = "info") {
  window.clearTimeout(toastTimer);
  toastElement.textContent = String(message || "");
  toastElement.dataset.type = type;
  toastElement.setAttribute("role", type === "error" ? "alert" : "status");
  toastElement.hidden = false;
  toastTimer = window.setTimeout(() => { toastElement.hidden = true; }, 4200);
}

function activeLeaveState() {
  if (!pageLeaveGuard) return null;
  try {
    const state = pageLeaveGuard.getState?.();
    if (!state || (!state.dirty && state.phase === "idle")) return null;
    return state;
  } catch (_error) {
    return { dirty: true, phase: "idle" };
  }
}

function canLeaveCurrentPage() {
  const state = activeLeaveState();
  if (!state) return true;
  if (state.phase === "saving" || state.phase === "deleting" || state.phase === "committed_unverified") {
    toast(
      state.phase === "committed_unverified"
        ? "変更は受付済みです。保存結果を確認中です。確認が完了するまでこの画面を離れられません。"
        : state.phase === "deleting"
        ? "削除結果を確認中です。完了するまでこの画面を離れられません。"
        : "保存結果を確認中です。完了するまでこの画面を離れられません。",
      "error",
    );
    return false;
  }
  return window.confirm("保存していない変更を破棄しますか？");
}

function registerLeaveGuard(ownerSignal, getState) {
  if (ownerSignal.aborted || typeof getState !== "function") return () => {};
  const guard = { getState };
  pageLeaveGuard = guard;
  const cleanup = () => {
    if (pageLeaveGuard === guard) pageLeaveGuard = null;
  };
  ownerSignal.addEventListener("abort", cleanup, { once: true });
  return cleanup;
}

function pageTitle(page) {
  return { overview: "全体サマリー", user: "ユーザー分析", management: "ユーザー管理" }[page];
}

function renderDateRange(state) {
  dateRangeHost.hidden = state.page === "management";
  document.querySelector("#managementRefreshButton").hidden = state.page !== "management";
  if (mainDateControl && mainDateControlPage === state.page) {
    mainDateControl.setAppliedRange(state);
    return;
  }
  mainDateControl?.destroy();
  mainDateControl = null;
  mainDateControlPage = state.page;
  if (dateRangeHost.hidden) {
    dateRangeHost.replaceChildren();
    return;
  }
  dateRangeHost.innerHTML = renderDateRangeControl("mainPeriod", state);
  mainDateControl = bindDateRangeControl(dateRangeHost, {
    range: state,
    onApply: (range) => {
      const current = stateFromUrl();
      navigate(current.page, { ...range, roster: current.roster, area: current.area, overviewPage: 1, userPage: 1 }, { replace: true });
    },
    onRefresh: () => { if (canLeaveCurrentPage()) void render({ forceAnalyticsRefresh: true }); },
  });
}

function exportRouteContext(state) {
  const isOverview = state.page === "overview";
  const isUser = state.page === "user";
  return {
    page: state.page,
    preset: state.preset,
    start: state.start,
    end: state.end,
    roster: isUser ? state.roster : "",
    area: isOverview ? state.area : "",
    q: isOverview ? state.overviewQuery : "",
    activity: isOverview ? state.overviewActivity : "",
    sort: isOverview ? state.overviewSort : "",
  };
}

function exportRouteKey(state) {
  return JSON.stringify(exportRouteContext(state));
}

function exportContextKey(state, snapshot) {
  return JSON.stringify({
    ...exportRouteContext(state),
    publishedRunId: String(snapshot?.publishedRunId || ""),
    rosterFingerprint: String(snapshot?.rosterFingerprint || ""),
    contentFingerprint: String(snapshot?.contentFingerprint || ""),
    scopePolicyVersion: String(snapshot?.scopePolicyVersion || ""),
    windowStart: String(snapshot?.windowStart || ""),
    windowEnd: String(snapshot?.windowEnd || ""),
    windowTimezone: String(snapshot?.windowTimezone || ""),
  });
}

function newIdempotencyKey() {
  return window.crypto?.randomUUID?.() || `csv-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function idempotencyKeyFor(contextKey) {
  if (exportRetry?.contextKey === contextKey) return exportRetry.idempotencyKey;
  exportRetry = { contextKey, idempotencyKey: newIdempotencyKey() };
  return exportRetry.idempotencyKey;
}

function bestEffortDeleteExportJob(jobId) {
  const normalizedJobId = String(jobId || "").trim();
  if (!normalizedJobId || exportCleanupJobs.has(normalizedJobId)) return;
  exportCleanupJobs.add(normalizedJobId);
  void deleteExportJob(normalizedJobId)
    .catch(() => {})
    .finally(() => exportCleanupJobs.delete(normalizedJobId));
}

function setExportIdle() {
  exportButton.disabled = !analyticsSnapshot?.publishedRunId;
  exportButton.removeAttribute("aria-busy");
}

function invalidateExportContext({ clearSnapshot = true, resetIdempotency = true } = {}) {
  const transaction = exportTransaction;
  if (transaction) {
    transaction.controller.abort();
    exportTransaction = null;
    if (transaction.jobId && transaction.phase !== "committed") {
      bestEffortDeleteExportJob(transaction.jobId);
    }
  }
  if (clearSnapshot) analyticsSnapshot = null;
  if (resetIdempotency) exportRetry = null;
  setExportIdle();
}

function isCurrentExportTransaction(transaction) {
  return exportTransaction === transaction
    && !transaction.controller.signal.aborted
    && exportContextKey(stateFromUrl(), analyticsSnapshot) === transaction.contextKey;
}

async function render({ focusMain = false, requestedState = null, navigation = null, forceAnalyticsRefresh = false } = {}) {
  const state = requestedState || stateFromUrl();
  if (!navigation) {
    renderedHistoryIndex = historyIndex;
    renderedUrl = dashboardUrl(state);
  }
  renderDateRange(navigation ? stateFromUrl() : state);
  document.title = `${pageTitle(state.page)} | OurA Navi User Analytics`;
  document.querySelectorAll(".mainNav [data-page]").forEach((button) => {
    const active = button.dataset.page === state.page;
    button.classList.toggle("isActive", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  const showPeriod = state.page !== "management";
  exportButton.hidden = !showPeriod;
  invalidateExportContext();
  if (state.page === "management" && activePage?.name === "management" && activePage.isCurrent()) {
    try { await activePage.instance.load(); }
    catch (error) { if (!isCancellation(error)) toast(error?.message || "画面を表示できませんでした。", "error"); }
    finally { if (activePage.isCurrent()) setBusy(root, false); }
    return;
  }
  if (
    state.page === "overview"
    && activePage?.name === "overview"
    && activePage.controller === renderController
    && !activePage.controller.signal.aborted
  ) {
    let committed = false;
    try {
      committed = await activePage.instance.refresh(state);
    } catch (error) {
      if (!isCancellation(error) && activePage.isCurrent()) toast(error?.message || "画面を表示できませんでした。", "error");
    }
    if (committed && navigation && activePage.isCurrent()) {
      const context = activePage.instance.currentContext();
      const committedState = {
        ...navigation.state,
        environmentRange: { ...activePage.instance.moduleRanges.environment },
        trendRange: { ...activePage.instance.moduleRanges.usage },
        preset: context.preset,
        area: context.areaKey,
        overviewQuery: context.query,
        overviewActivity: context.activity,
        overviewSort: context.sort,
        overviewPage: context.page,
      };
      writeState(committedState, navigation.options);
      renderedHistoryIndex = historyIndex;
      renderedUrl = dashboardUrl(committedState);
      activePage.preset = context.preset;
    }
    const currentState = stateFromUrl();
    renderDateRange(currentState);
    renderedUrl = dashboardUrl(currentState);
    if (focusMain && activePage.isCurrent() && !document.querySelector("[data-range-popup]:popover-open")) root.focus({ preventScroll: true });
    return;
  }
  const reusableUserPage = activePage;
  if (
    state.page === "user"
    && reusableUserPage?.name === "user"
    && reusableUserPage.controller === renderController
    && !reusableUserPage.controller.signal.aborted
  ) {
    let committed = false;
    try {
      committed = await reusableUserPage.instance.transition(state, {
        forceAnchor: forceAnalyticsRefresh || reusableUserPage.preset !== state.preset || reusableUserPage.instance.start !== state.start || reusableUserPage.instance.end !== state.end,
      });
    } catch (error) {
      if (!isCancellation(error) && reusableUserPage.isCurrent()) toast(error?.message || "画面を表示できませんでした。", "error");
    }
    if (activePage !== reusableUserPage || !reusableUserPage.isCurrent()) return;
    if (committed && navigation) {
      const route = reusableUserPage.instance.routeState();
      const committedState = {
        ...navigation.state,
        preset: route.preset,
        start: route.start,
        end: route.end,
        roster: route.roster,
        userQuery: route.userQuery,
        userPage: route.userPage,
      };
      writeState(committedState, navigation.options);
      renderedHistoryIndex = historyIndex;
      renderedUrl = dashboardUrl(committedState);
    }
    reusableUserPage.analyticsAsOf = reusableUserPage.instance.analysisAsOf;
    reusableUserPage.preset = reusableUserPage.instance.preset;
    const currentState = stateFromUrl();
    renderDateRange(currentState);
    renderedUrl = dashboardUrl(currentState);
    if (focusMain && !document.querySelector("[data-range-popup]:popover-open")) root.focus({ preventScroll: true });
    return;
  }
  const analyticsAsOf = state.page === "user"
    ? (
      activePage?.name === "user"
      && activePage.preset === state.preset
      && !activePage.controller.signal.aborted
        ? activePage.analyticsAsOf
        : new Date().toISOString()
    )
    : "";
  renderController?.abort();
  const controller = new AbortController();
  renderController = controller;
  const generation = ++renderGeneration;
  const isCurrent = () => generation === renderGeneration && !controller.signal.aborted;
  destroyAllCharts();
  root.innerHTML = moduleMessage("読み込み中…", "loading");
  setBusy(root, true);
  const shared = {
    navigate,
    toast,
    state,
    getPreset: () => stateFromUrl().preset,
    analyticsAsOf,
    signal: controller.signal,
    isCurrent,
    setArea: (area) => {
      const currentState = stateFromUrl();
      return navigate("overview", { preset: currentState.preset, area, overviewPage: 1 });
    },
    clearManagementRoster,
    setLeaveGuard: (getState) => registerLeaveGuard(controller.signal, getState),
    setAnalyticsSnapshot: (metadata) => {
      if (!isCurrent()) return;
      if (!metadata) {
        invalidateExportContext();
        return;
      }
      const expectedScope = state.page === "overview" ? "global" : "user_map";
      if (!metadata?.publishedRunId || metadata.scope !== expectedScope) return;
      const currentState = stateFromUrl();
      if (
        analyticsSnapshot
        && exportContextKey(currentState, analyticsSnapshot) !== exportContextKey(currentState, metadata)
      ) {
        invalidateExportContext();
      }
      analyticsSnapshot = { ...metadata };
      exportButton.disabled = Boolean(exportTransaction);
    },
  };
  const instance = state.page === "overview"
    ? new OverviewPage(root, shared)
    : state.page === "user"
      ? new UserAnalysisPage(root, { ...shared, rosterId: state.roster })
      : new UserManagementPage(root, { ...shared, rosterId: state.roster });
  activePage = { name: state.page, preset: state.preset, analyticsAsOf, instance, controller, isCurrent };
  try {
    await instance.load();
  } catch (error) {
    if (!isCancellation(error) && isCurrent()) toast(error?.message || "画面を表示できませんでした。", "error");
  } finally {
    if (isCurrent()) setBusy(root, false);
  }
  if (focusMain && isCurrent() && !document.querySelector("[data-range-popup]:popover-open")) root.focus({ preventScroll: true });
}

document.querySelectorAll(".mainNav [data-page]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.page)));
document.querySelector("#managementRefreshButton").addEventListener("click", () => {
  if (canLeaveCurrentPage()) void render({ forceAnalyticsRefresh: true });
});
window.addEventListener("popstate", (event) => {
  const targetIndex = Number.isInteger(event.state?.[HISTORY_INDEX_KEY])
    ? event.state[HISTORY_INDEX_KEY]
    : null;
  if (restoringHistory) {
    restoringHistory = false;
    historyIndex = targetIndex ?? renderedHistoryIndex;
    return;
  }
  historyIndex = targetIndex ?? historyIndex;
  if (!canLeaveCurrentPage()) {
    if (targetIndex != null && targetIndex !== renderedHistoryIndex) {
      restoringHistory = true;
      window.history.go(renderedHistoryIndex - targetIndex);
    } else if (targetIndex == null) {
      historyIndex = renderedHistoryIndex;
      window.history.replaceState(
        { ...(window.history.state || {}), [HISTORY_INDEX_KEY]: renderedHistoryIndex },
        "",
        renderedUrl,
      );
    }
    return;
  }
  render({ focusMain: true });
});
window.addEventListener("beforeunload", (event) => {
  if (!activeLeaveState()) return;
  event.preventDefault();
  event.returnValue = "";
});

exportButton.addEventListener("click", async () => {
  const state = stateFromUrl();
  if (state.page === "management") return;
  if (state.page === "user" && !state.roster) { toast("先にユーザーを選択してください。", "error"); return; }
  if (!analyticsSnapshot?.publishedRunId) { toast("画面の分析データを読み込んでからCSVを作成してください。", "error"); return; }
  const snapshot = { ...analyticsSnapshot };
  const contextKey = exportContextKey(state, snapshot);
  invalidateExportContext({ clearSnapshot: false, resetIdempotency: false });
  const controller = new AbortController();
  const transaction = {
    controller,
    contextKey,
    idempotencyKey: idempotencyKeyFor(contextKey),
    jobId: "",
    phase: "creating",
  };
  exportTransaction = transaction;
  exportButton.disabled = true;
  exportButton.setAttribute("aria-busy", "true");
  try {
    const job = exportJobModel(await createExportJob({
      kind: state.page === "user" ? "user_detail" : "overview_users",
      rosterId: state.roster,
      ...requestDateRange(state),
      areaKey: state.area,
      q: state.overviewQuery,
      activity: state.overviewActivity,
      sort: state.overviewSort,
      expectedPublishedRunId: snapshot.publishedRunId,
      expectedRosterFingerprint: snapshot.rosterFingerprint,
      expectedContentFingerprint: snapshot.contentFingerprint,
      expectedScopePolicyVersion: snapshot.scopePolicyVersion,
      expectedWindowStart: snapshot.windowStart,
      expectedWindowEnd: snapshot.windowEnd,
      expectedWindowTimezone: snapshot.windowTimezone,
      idempotencyKey: transaction.idempotencyKey,
    }, { signal: controller.signal }));
    transaction.jobId = job.jobId;
    transaction.phase = "ready";
    if (!isCurrentExportTransaction(transaction)) {
      bestEffortDeleteExportJob(job.jobId);
      return;
    }
    transaction.phase = "downloading";
    const blob = await downloadExportJob(job.downloadUrl, { signal: controller.signal });
    if (!isCurrentExportTransaction(transaction)) {
      bestEffortDeleteExportJob(job.jobId);
      return;
    }
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = job.filename;
    document.body.appendChild(link);
    if (!isCurrentExportTransaction(transaction)) {
      link.remove();
      URL.revokeObjectURL(objectUrl);
      bestEffortDeleteExportJob(job.jobId);
      return;
    }
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    transaction.phase = "committed";
    if (exportTransaction === transaction) {
      exportTransaction = null;
      setExportIdle();
    }
    toast(`CSVをダウンロードしました（${job.rowCount}行）。`, "success");
    bestEffortDeleteExportJob(job.jobId);
  } catch (error) {
    if (transaction.jobId) bestEffortDeleteExportJob(transaction.jobId);
    if (!isCancellation(error) && isCurrentExportTransaction(transaction)) toast(error.message, "error");
  } finally {
    if (exportTransaction === transaction) {
      exportTransaction = null;
      setExportIdle();
    }
  }
});

writeState(stateFromUrl(), { replace: true });
render();
