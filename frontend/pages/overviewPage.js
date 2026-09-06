import { getOverview, getOverviewUsers, getRegions, getEnvironment, getUsageTrend, getNewsUsageOverview, isCancellation } from "../api/client.js";
import {
  activityModel, environmentModel, kpisModel, overviewEnvelope,
  productsModel, regionsModel, taskModel, usageTrendModel,
} from "../adapters/overviewAdapter.js";
import { usersModel } from "../adapters/usersAdapter.js";
import { newsUsageDashboardModel } from "../adapters/newsUsageDashboardAdapter.js";
import { newsDashboardTrendChart, newsDashboardShareChart, newsCategoryRankingChart } from "../components/newsUsageCharts.js";
import { renderDateRangeControl, bindDateRangeControl, normalizeDateRange, requestDateRange, periodLabel } from "../components/dateRangeControl.js";
import {
  barChart, destroyChartCanvases, destroyChartsInRoot, doughnutChart, stackedChart, usageTrendChart,
} from "../components/charts.js";
import { bindPagination, bindResponsiveCollection, paginate, paginationMarkup } from "../components/collection.js";
import { chips, escapeHtml, moduleMessage, setBusy } from "../components/dom.js";
import { renderJapanMap } from "../components/japanMap.js";
import { renderProductMatrix } from "../components/productMatrix.js";
import {
  displayCount, displayDateTime, displayMeasuredDuration, displayMeasuredRate,
  displayRate,
} from "../viewModels/formatters.js";

const OVERVIEW_MODULES = ["kpis", "activity", "products"];
const MAX_SNAPSHOT_ATTEMPTS = 3;
const MAX_COMMITTED_ANCHOR_AGE_MS = 4 * 60 * 1000;

function capturedModel(create) {
  try { return { status: "fulfilled", value: create() }; }
  catch (reason) { return { status: "rejected", reason }; }
}

export class OverviewPage {
  constructor(root, { navigate, state, signal, isCurrent, setArea, setAnalyticsSnapshot }) {
    this.root = root;
    this.navigate = navigate;
    this.preset = state.preset;
    this.start = state.start || "";
    this.end = state.end || "";
    this.moduleRanges = {
      environment: normalizeDateRange(state.environmentRange || state),
      usage: normalizeDateRange(state.trendRange || state),
    };
    this.moduleRequests = new Map();
    this.moduleModels = new Map();
    this.moduleErrors = new Map();
    this.moduleDateControls = new Map();
    this.refreshSequence = 0;
    this.areaKey = state.area;
    this.signal = signal;
    this.isCurrent = isCurrent;
    this.setArea = setArea;
    this.setAnalyticsSnapshot = setAnalyticsSnapshot;
    this.query = state.overviewQuery;
    this.activity = state.overviewActivity;
    this.sort = state.overviewSort;
    this.page = state.overviewPage;
    this.userModel = null;
    this.snapshotKey = "";
    this.committedReceiptGeneration = "none";
    this.hasCommittedBody = false;
    this.hasCommittedSnapshot = false;
    this.snapshotMismatch = false;
    this.snapshotTransactionGeneration = 0;
    this.operationGeneration = 0;
    this.userSearchTimer = 0;
    this.userRefreshError = "";
    this.overviewRefreshError = "";
    this.committedAsOf = "";
    this.stagedRenderFailed = false;
    this.initialContext = this.contextFromState(state);
    this.signal.addEventListener("abort", () => {
      window.clearTimeout(this.userSearchTimer);
      for (const request of this.moduleRequests.values()) request.controller.abort();
      this.moduleDateControls.forEach((control) => control?.destroy());
    }, { once: true });
    this.compactCollection = bindResponsiveCollection(this.signal, () => this.renderUserRows());
  }

  async load() {
    this.root.innerHTML = this.shell();
    this.installUserControls();
    this.installModuleDateControls();
    return this.refreshAll(this.initialContext);
  }

  async refresh(state) {
    const previous = JSON.stringify(this.moduleRanges);
    if (state.environmentRange) this.moduleRanges.environment = normalizeDateRange(state.environmentRange);
    if (state.trendRange) this.moduleRanges.usage = normalizeDateRange(state.trendRange);
    const rangesChanged = previous !== JSON.stringify(this.moduleRanges);
    return this.refreshAll(this.contextFromState(state), { rangesChanged });
  }

  async refreshAll(context, { rangesChanged = false } = {}) {
    const sequence = ++this.refreshSequence;
    const independent = !this.hasCommittedBody || context.areaKey !== this.areaKey || rangesChanged;
    const modules = independent ? ["environment", "usage", "news"] : ["news"];
    const results = await Promise.allSettled([
      this.runSnapshotTransaction(context),
      ...modules.map((name) => this.refreshModule(name, name === "news" ? context : this.moduleRanges[name], { deferCommit: true, areaKey: context.areaKey })),
    ]);
    if (!this.isCurrent() || sequence !== this.refreshSequence) return false;
    const canCommitAuxiliary = results[0].status === "fulfilled" && (results[0].value || !this.hasCommittedBody);
    const refreshedModules = [];
    if (canCommitAuxiliary) {
      modules.forEach((name, index) => {
        const outcome = results[index + 1];
        if (outcome.status !== "fulfilled" || !outcome.value) return;
        if (this.moduleRequests.get(name) !== outcome.value.request) return;
        if (outcome.value.model) {
          this.moduleModels.set(name, outcome.value.model);
          this.moduleErrors.delete(name);
        } else if (outcome.value.error) this.moduleErrors.set(name, outcome.value.error);
        refreshedModules.push(name);
      });
    }
    if (canCommitAuxiliary) {
      refreshedModules.forEach((name) => this.renderAuxiliaryModule(name));
      if (rangesChanged) this.installModuleDateControls();
    }
    if (results[0].status === "rejected") throw results[0].reason;
    return results[0].value;
  }

  contextFromState(state) {
    return {
      preset: state.preset || "last_7d",
      start: state.start || "",
      end: state.end || "",
      areaKey: state.area || "",
      query: state.overviewQuery || "",
      activity: state.overviewActivity || "",
      sort: state.overviewSort || "last_desc",
      page: Math.max(1, Number(state.overviewPage) || 1),
    };
  }

  currentContext() {
    return {
      preset: this.preset,
      start: this.start,
      end: this.end,
      areaKey: this.areaKey,
      query: this.query,
      activity: this.activity,
      sort: this.sort,
      page: this.page,
    };
  }

  commitContext(context) {
    this.preset = context.preset;
    this.start = context.start || "";
    this.end = context.end || "";
    this.areaKey = context.areaKey;
    this.query = context.query;
    this.activity = context.activity;
    this.sort = context.sort;
    this.page = context.page;
    const period = this.root.querySelector("[data-overview-period]");
    if (period) period.textContent = `${periodLabel(this.currentContext())}の利用実態を、採用から個人まで順に確認できます。`;
    const search = this.root.querySelector("#overviewUserSearch");
    const activity = this.root.querySelector("#overviewActivity");
    const sort = this.root.querySelector("#overviewSort");
    if (search) search.value = this.query;
    if (activity) activity.value = this.activity;
    if (sort) sort.value = this.sort;
  }

  restoreCommittedScope() {
    this.navigate("overview", {
      preset: this.preset,
      start: this.start,
      end: this.end,
      area: this.areaKey,
      overviewQuery: this.query,
      overviewActivity: this.activity,
      overviewSort: this.sort,
      overviewPage: this.page,
    }, { replace: true, render: false });
  }

  async runSnapshotTransaction(context) {
    setBusy(this.root, true);
    const transactionGeneration = this.snapshotTransactionGeneration + 1;
    try {
      return await this.loadSnapshotTransaction(context);
    } finally {
      if (this.isCurrent() && transactionGeneration === this.snapshotTransactionGeneration) {
        setBusy(this.root, false);
      }
    }
  }

  shell() {
    return `
      <div class="pageHeading overviewHeading">
        <div><p class="eyebrow">利用状況・定着・ニーズ</p><h2>全体サマリー</h2><p data-overview-period>${escapeHtml(periodLabel(this.currentContext()))}の利用実態を、採用から個人まで順に確認できます。</p></div>
        <div class="scopeSummary"><span data-summary-total>全体サマリー対象を読込中</span><span data-summary-selection hidden></span><div id="areaChip"></div></div>
      </div>
      <div class="moduleRefreshError" data-freshness-banner hidden></div>
      <section class="panel priorityPanel" data-module="kpis"><div class="panelHead"><div><p class="sectionIndex">01</p><h3>主要KPI</h3></div><small>本社MR・コントラクトMR</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="panel" data-module="environment"><div class="panelHead"><div><p class="sectionIndex">02</p><h3>利用環境・モード</h3></div></div><div class="moduleDateControls" data-module-date="environment">${renderDateRangeControl("environment-period", this.moduleRanges.environment, { compact: true })}</div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="twoGrid insightGrid"><article class="panel" data-module="usage"><div class="panelHead"><div><p class="sectionIndex">03</p><h3>利用推移</h3></div></div><div class="moduleDateControls" data-module-date="usage">${renderDateRangeControl("usage-period", this.moduleRanges.usage, { compact: true })}</div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article><article class="panel" data-module="tasks"><div class="panelHead"><div><p class="sectionIndex">03</p><h3>質問種類</h3></div><small>利用推移と同じ期間</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article></section>
      <section class="panel" data-module="activity"><div class="panelHead"><div><p class="sectionIndex">04</p><h3>活性度分布 <details class="activityHelp"><summary aria-label="活性度の定義">?</summary><div class="activityHelpContent"><strong>直近14日の質問した日数</strong><p>選択期間の最終日までの14日間を対象に、質問した日を1日ずつ数えます。</p><dl><div><dt>高アクティブ</dt><dd>6日以上</dd></div><div><dt>中アクティブ</dt><dd>3〜5日</dd></div><div><dt>低アクティブ</dt><dd>1〜2日</dd></div><div><dt>休眠ユーザー</dt><dd>0日</dd></div></dl></div></details></h3></div></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="panel" data-module="users"><div class="panelHead"><div><p class="sectionIndex">05</p><h3>ユーザー一覧</h3></div><small>選択期間内の利用状況</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="twoGrid mapGrid"><article class="panel" data-module="map"><div class="panelHead"><div><p class="sectionIndex">06</p><h3>日本利用マップ</h3></div><small>色の濃さ: 利用率</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article><article class="panel" data-module="ranking"><div class="panelHead"><h3>地域ランキング</h3><small>利用率順</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article></section>
      <section class="panel" data-module="products"><div class="panelHead"><div><p class="sectionIndex">07</p><h3>製品ニーズ</h3></div><small>製品 Top 10 × 質問種類</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="twoGrid newsUsageDashboard" aria-label="ニュース・学会の利用状況">
        <article class="panel" data-module="newsTrend"><div class="panelHead"><div><p class="sectionIndex">08</p><h3>ニュース・学会 利用推移</h3></div></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article>
        <article class="panel" data-module="newsShare"><div class="panelHead"><div><p class="sectionIndex">09</p><h3>ニュース・学会 クリック割合</h3></div></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article>
        <article class="panel" data-module="newsCategories"><div class="panelHead"><div><p class="sectionIndex">10</p><h3>ニュース分類ランキング</h3></div></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article>
        <article class="panel" data-module="societyCategories"><div class="panelHead"><div><p class="sectionIndex">11</p><h3>学会カテゴリランキング</h3></div></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article>
      </section>`;
  }

  body(name) { return this.root.querySelector(`[data-module="${name}"] [data-module-body]`); }

  installModuleDateControls(names = ["environment", "usage"]) {
    for (const name of names) {
      this.moduleDateControls.get(name)?.destroy();
      const host = this.root.querySelector(`[data-module-date="${name}"]`);
      if (!host) continue;
      host.innerHTML = renderDateRangeControl(`${name}-period`, this.moduleRanges[name], { compact: true });
      this.moduleDateControls.set(name, bindDateRangeControl(host, {
        range: this.moduleRanges[name], signal: this.signal,
        onApply: (range) => this.refreshModule(name, range, { persist: true }),
        onRefresh: () => this.refreshModule(name, this.moduleRanges[name]),
      }));
    }
  }

  moduleNames(name) {
    if (name === "news") return ["newsTrend", "newsShare", "newsCategories", "societyCategories"];
    return name === "usage" ? ["usage", "tasks"] : [name];
  }

  async refreshModule(name, selectedRange, { persist = false, deferCommit = false, areaKey = this.areaKey } = {}) {
    this.moduleRequests.get(name)?.controller.abort();
    const request = { controller: new AbortController(), pending: true };
    this.moduleRequests.set(name, request);
    const range = normalizeDateRange(selectedRange);
    const panelBusy = (busy) => this.moduleNames(name).forEach((part) => setBusy(this.root.querySelector(`[data-module="${part}"]`), busy));
    panelBusy(true);
    try {
      const params = { ...requestDateRange(range, new Date().toISOString()), ...(areaKey ? { area_key: areaKey } : {}) };
      const api = name === "environment" ? getEnvironment : name === "usage" ? getUsageTrend : getNewsUsageOverview;
      const raw = await api(params, { signal: request.controller.signal });
      if (!this.isCurrent() || this.moduleRequests.get(name) !== request) return false;
      if (name !== "news" && raw.scope !== "global") throw new Error("データを取得できませんでした。");
      const model = name === "environment" ? environmentModel(raw)
        : name === "usage" ? { usage: usageTrendModel(raw), tasks: taskModel(raw) }
        : newsUsageDashboardModel(raw);
      if (deferCommit) return { model, range, request };
      this.moduleModels.set(name, model);
      this.moduleErrors.delete(name);
      if (persist) {
        this.moduleRanges[name] = range;
        this.navigate("overview", {
          [name === "environment" ? "environmentRange" : "trendRange"]: range,
        }, { replace: true, render: false });
      }
      this.renderAuxiliaryModule(name);
      if (persist) this.installModuleDateControls([name]);
      return true;
    } catch (error) {
      if (isCancellation(error) || !this.isCurrent() || this.moduleRequests.get(name) !== request) return false;
      const message = this.moduleModels.has(name)
        ? "更新できませんでした。表示中の内容を保持しています。"
        : "データを取得できませんでした。再読込してください。";
      if (deferCommit) return { error: message, request };
      this.moduleErrors.set(name, message);
      this.renderAuxiliaryModule(name);
      return false;
    } finally {
      request.pending = false;
      if (this.isCurrent() && this.moduleRequests.get(name) === request) panelBusy(false);
    }
  }

  renderAuxiliaryModules() {
    for (const name of ["environment", "usage", "news"]) this.renderAuxiliaryModule(name);
  }

  renderAuxiliaryModule(name) {
    const model = this.moduleModels.get(name);
    const error = this.moduleErrors.get(name);
    for (const part of this.moduleNames(name)) {
      const body = this.body(part);
      if (!body) continue;
      destroyChartsInRoot(body);
      body.replaceChildren();
    }
    if (!model) {
      for (const part of this.moduleNames(name)) {
        const body = this.body(part);
        if (body) body.innerHTML = moduleMessage(error || "読み込み中…", error ? "error" : "loading");
      }
    } else {
      try {
        if (name === "environment") this.renderEnvironment(model);
        else if (name === "usage") { this.renderUsage(model.usage); this.renderTasks(model.tasks); }
        else this.renderNewsDashboard(model);
      } catch (_error) {
        for (const part of this.moduleNames(name)) this.fail(part, new Error("グラフを表示できませんでした。再読込してください。"));
      }
      if (error) {
        for (const part of this.moduleNames(name)) this.body(part)?.insertAdjacentHTML("beforeend", moduleMessage(error, "error"));
      }
    }
    if (this.moduleRequests.get(name)?.pending) {
      for (const part of this.moduleNames(name)) setBusy(this.root.querySelector(`[data-module="${part}"]`), true);
    }
  }

  renderNewsDashboard(model) {
    if (!model.available) {
      const unavailable = model.state.availability === "unavailable";
      const message = unavailable ? "利用状況を取得できませんでした。"
        : model.state.availability === "before_measurement" ? "選択した期間のデータはありません。"
        : "利用データはまだありません。";
      for (const part of this.moduleNames("news")) this.body(part).innerHTML = moduleMessage(message, unavailable ? "error" : "empty");
      return;
    }
    const render = (name, hasRows, create, { tall = false } = {}) => {
      const body = this.body(name);
      body.innerHTML = hasRows ? `<div class="chartBox ${tall ? "tall" : "primaryChart"}"><canvas></canvas></div>` : moduleMessage("この期間の利用記録はありません。", "empty");
      if (hasRows) create(body.querySelector("canvas"));
    };
    render("newsTrend", model.trend.length > 0, (canvas) => newsDashboardTrendChart(canvas, model.trend));
    render("newsShare", model.totals.contentClicks > 0, (canvas) => newsDashboardShareChart(canvas, model.totals));
    render("newsCategories", model.newsCategories.some((row) => row.clicks > 0), (canvas) => newsCategoryRankingChart(canvas, model.newsCategories), { tall: true });
    render("societyCategories", model.societyCategories.some((row) => row.clicks > 0), (canvas) => newsCategoryRankingChart(canvas, model.societyCategories, { society: true }), { tall: true });
  }

  fail(name, error) {
    if (!this.isCurrent() || isCancellation(error)) return;
    const target = this.body(name);
    if (target) target.innerHTML = moduleMessage(error?.message || "データを取得できませんでした。", "error");
  }

  renderPart(name, render) {
    try { render(); } catch (error) {
      if (this.hasCommittedBody) throw error;
      this.stagedRenderFailed = true;
      destroyChartsInRoot(this.body(name));
      this.fail(name, error);
    }
  }

  renderSnapshotIssue() {
    if (!this.snapshotMismatch) return;
    const banner = this.root.querySelector("[data-freshness-banner]");
    if (!banner || banner.querySelector("[data-snapshot-mismatch]")) return;
    const notice = document.createElement("span");
    notice.dataset.snapshotMismatch = "true";
    notice.textContent = "データを更新できませんでした。再読込してください。";
    banner.hidden = false;
    banner.appendChild(notice);
  }

  clearSnapshotIssue() {
    this.root.querySelector("[data-snapshot-mismatch]")?.remove();
    const banner = this.root.querySelector("[data-freshness-banner]");
    if (banner && !banner.children.length) banner.hidden = true;
  }

  renderOverviewRefreshIssue() {
    const banner = this.root.querySelector("[data-freshness-banner]");
    if (!banner) return;
    let notice = banner.querySelector("[data-overview-refresh-error]");
    if (!notice) {
      notice = document.createElement("span");
      notice.dataset.overviewRefreshError = "true";
      notice.setAttribute("role", "alert");
      banner.appendChild(notice);
    }
    notice.textContent = this.overviewRefreshError;
    banner.hidden = false;
  }

  clearOverviewRefreshIssue() {
    this.overviewRefreshError = "";
    this.root.querySelector("[data-overview-refresh-error]")?.remove();
    const banner = this.root.querySelector("[data-freshness-banner]");
    if (banner && !banner.children.length) banner.hidden = true;
  }

  preserveCommittedRefreshFailure() {
    this.snapshotMismatch = false;
    this.clearSnapshotIssue();
    this.restoreCommittedScope();
    this.overviewRefreshError = "最新データを取得できませんでした。表示中の内容を保持しています。";
    this.renderOverviewRefreshIssue();
    this.setAnalyticsSnapshot(null);
  }

  renderInitialRenderFailure(error) {
    destroyChartsInRoot(this.root);
    this.root.innerHTML = this.shell();
    this.installUserControls();
    this.installModuleDateControls();
    this.overviewRefreshError = "画面を表示できませんでした。再読込してください。";
    this.renderOverviewRefreshIssue();
    [...OVERVIEW_MODULES, "map", "ranking"].forEach((name) => this.fail(name, error));
    const users = this.body("users")?.querySelector("#overviewUserResults");
    if (users) users.innerHTML = moduleMessage(error?.message || "画面を描画できませんでした。", "error");
    this.setAnalyticsSnapshot(null);
  }

  snapshotEntries(results) {
    return results.map((result, index) => {
      if (result.status !== "fulfilled") return { index, receipt: "rejected", snapshotKey: "" };
      const metadata = index === 0
        ? result.value.envelope.scopeMetadata
        : result.value.scopeMetadata;
      return metadata?.available && metadata.snapshotKey
        ? { index, receipt: "complete", snapshotKey: metadata.snapshotKey }
        : { index, receipt: "legacy", snapshotKey: "" };
    });
  }

  coherentSnapshotDecision(results) {
    const entries = this.snapshotEntries(results);
    const fulfilled = entries.filter((entry) => entry.receipt !== "rejected");
    if (!fulfilled.length) {
      return {
        coherent: true,
        acceptedIndices: new Set(),
        snapshotKey: "",
        receiptGeneration: "none",
        allowExport: false,
      };
    }
    if (fulfilled.every((entry) => entry.receipt === "legacy")) {
      return {
        // A legacy response cannot authorize CSV or prove a cross-module
        // snapshot, but that missing receipt is not permission to erase
        // otherwise usable historical bodies. Render every fulfilled module
        // with the explicit legacy notice and keep export closed.
        coherent: true,
        acceptedIndices: new Set(fulfilled.map((entry) => entry.index)),
        snapshotKey: "",
        receiptGeneration: "legacy",
        allowExport: false,
      };
    }
    const completeKeys = new Set(
      fulfilled.filter((entry) => entry.receipt === "complete").map((entry) => entry.snapshotKey),
    );
    if (fulfilled.every((entry) => entry.receipt === "complete") && completeKeys.size === 1) {
      return {
        coherent: true,
        acceptedIndices: new Set(fulfilled.map((entry) => entry.index)),
        snapshotKey: fulfilled[0].snapshotKey,
        receiptGeneration: "complete",
        // A failed sibling API may be rendered as a local module error on the
        // first load, but it is not a complete export snapshot.
        allowExport: fulfilled.length === entries.length,
      };
    }
    return {
      coherent: false,
      acceptedIndices: new Set(),
      snapshotKey: "",
      receiptGeneration: "none",
      allowExport: false,
    };
  }

  boundedSnapshotDecision(results) {
    const entries = this.snapshotEntries(results);
    let snapshotKey = entries[0]?.receipt === "complete" ? entries[0].snapshotKey : "";
    if (!snapshotKey) {
      const counts = new Map();
      entries.slice(1).forEach((entry) => {
        if (entry.receipt === "complete") counts.set(entry.snapshotKey, (counts.get(entry.snapshotKey) || 0) + 1);
      });
      const ranked = [...counts.entries()].sort((left, right) => right[1] - left[1]);
      if (ranked[0]?.[1] >= 2 && ranked[0][1] !== ranked[1]?.[1]) snapshotKey = ranked[0][0];
    }
    return {
      snapshotKey,
      receiptGeneration: snapshotKey ? "complete" : "none",
      acceptedIndices: new Set(entries.flatMap((entry) => (
        snapshotKey && entry.receipt === "complete" && entry.snapshotKey === snapshotKey
          ? [entry.index]
          : []
      ))),
      allowExport: false,
    };
  }

  async fetchSnapshotSet(context, asOf) {
    return Promise.allSettled([
      getOverview(
        { ...requestDateRange(context, asOf), area_key: context.areaKey },
        { signal: this.signal },
      ).then((raw) => ({
        envelope: overviewEnvelope(raw),
        models: {
          kpis: capturedModel(() => kpisModel(raw)),
          activity: capturedModel(() => activityModel(raw)),
          products: capturedModel(() => productsModel(raw)),
        },
      })),
      getRegions(
        requestDateRange(context, asOf),
        { signal: this.signal },
      ).then((raw) => regionsModel(raw)),
      getOverviewUsers({
        ...requestDateRange(context, asOf),
        area_key: context.areaKey,
        q: context.query,
        activity: context.activity,
        sort: context.sort,
      }, { signal: this.signal }).then((raw) => usersModel(raw, "global")),
    ]);
  }

  async prepareMapStage(results, acceptedIndices, context) {
    const regionsResult = results[1];
    if (regionsResult.status !== "fulfilled" || !acceptedIndices.has(1)) {
      return { container: null, legend: null, error: null };
    }
    try {
      const container = document.createElement("div");
      container.id = "japanMap";
      container.className = "mapCanvas";
      await renderJapanMap(container, regionsResult.value.regions, {
        selectedAreaKey: context.areaKey,
        onSelect: (key) => this.setArea(key === this.areaKey ? "" : key),
        signal: this.signal,
      });
      const legend = document.createElement("div");
      legend.className = "mapLegend";
      legend.setAttribute("aria-label", "地図の色の説明");
      legend.innerHTML = "<span>低い</span><i></i><span>高い</span>";
      return { container, legend, error: null };
    } catch (error) {
      return { container: null, legend: null, error };
    }
  }

  async loadSnapshotTransaction(context = this.currentContext()) {
    const transactionGeneration = ++this.snapshotTransactionGeneration;
    const operationGeneration = ++this.operationGeneration;
    this.setAnalyticsSnapshot(null);
    const asOf = new Date().toISOString();
    let lastResults = null;
    for (let attempt = 0; attempt < MAX_SNAPSHOT_ATTEMPTS; attempt += 1) {
      const results = await this.fetchSnapshotSet(context, asOf);
      if (
        !this.isCurrent()
        || transactionGeneration !== this.snapshotTransactionGeneration
        || operationGeneration !== this.operationGeneration
      ) return false;
      lastResults = results;
      if (this.hasCommittedBody && results.some((result) => result.status !== "fulfilled")) {
        this.preserveCommittedRefreshFailure();
        return false;
      }
      if (
        this.hasCommittedBody
        && results[0].status === "fulfilled"
        && Object.values(results[0].value.models).some((result) => result.status === "rejected")
      ) {
        this.preserveCommittedRefreshFailure();
        return false;
      }
      const decision = this.coherentSnapshotDecision(results);
      if (!decision.coherent) continue;
      if (this.hasCommittedSnapshot && decision.receiptGeneration === "legacy") {
        this.snapshotMismatch = true;
        this.clearOverviewRefreshIssue();
        this.restoreCommittedScope();
        this.renderSnapshotIssue();
        this.setAnalyticsSnapshot(null);
        return false;
      }
      const mapStage = await this.prepareMapStage(results, decision.acceptedIndices, context);
      if (
        !this.isCurrent()
        || transactionGeneration !== this.snapshotTransactionGeneration
        || operationGeneration !== this.operationGeneration
      ) return false;
      if (mapStage.error && this.hasCommittedBody) {
        this.preserveCommittedRefreshFailure();
        return false;
      }
      const diagnosticAt = (index) => {
        const result = results[index];
        if (result?.status !== "fulfilled") return null;
        if (index === 0) return result.value.envelope.contentDiagnostics;
        return result.value.contentDiagnostics;
      };
      const diagnosticsHealthy = [...decision.acceptedIndices]
        .every((index) => diagnosticAt(index)?.exportAvailable === true);
      const overviewModelsHealthy = results[0]?.status === "fulfilled"
        && Object.values(results[0].value.models).every((result) => result.status === "fulfilled");
      const commitDecision = {
        ...decision,
        allowExport: decision.allowExport && diagnosticsHealthy && overviewModelsHealthy && !mapStage.error,
      };
      try {
        const committed = await this.commitSnapshotResults(
          results,
          transactionGeneration,
          operationGeneration,
          commitDecision,
          context,
          asOf,
          mapStage,
        );
        if (!committed) return false;
        this.snapshotKey = decision.snapshotKey;
        this.snapshotMismatch = false;
        return true;
      } catch (error) {
        if (isCancellation(error)) throw error;
        if (this.hasCommittedBody) this.preserveCommittedRefreshFailure();
        else this.renderInitialRenderFailure(error);
        return false;
      }
    }
    if (
      !this.isCurrent()
      || transactionGeneration !== this.snapshotTransactionGeneration
      || operationGeneration !== this.operationGeneration
    ) return false;
    this.snapshotMismatch = true;
    const error = new Error("データを更新できませんでした。再読込してください。");
    if (this.hasCommittedBody) {
      this.clearOverviewRefreshIssue();
      this.restoreCommittedScope();
      this.renderSnapshotIssue();
      this.setAnalyticsSnapshot(null);
      return false;
    }
    const decision = this.boundedSnapshotDecision(lastResults);
    const mapStage = await this.prepareMapStage(lastResults, decision.acceptedIndices, context);
    if (
      !this.isCurrent()
      || transactionGeneration !== this.snapshotTransactionGeneration
      || operationGeneration !== this.operationGeneration
    ) return false;
    try {
      const committed = await this.commitSnapshotResults(
        lastResults,
        transactionGeneration,
        operationGeneration,
        { ...decision, mismatchError: error },
        context,
        asOf,
        mapStage,
      );
      if (!committed) return false;
      this.snapshotKey = decision.snapshotKey;
    } catch (renderError) {
      if (isCancellation(renderError)) throw renderError;
      this.renderInitialRenderFailure(renderError);
      return false;
    }
    if (
      !this.isCurrent()
      || transactionGeneration !== this.snapshotTransactionGeneration
      || operationGeneration !== this.operationGeneration
    ) return false;
    this.renderSnapshotIssue();
    this.setAnalyticsSnapshot(null);
    return false;
  }

  async commitSnapshotResults(results, transactionGeneration, operationGeneration, {
    acceptedIndices,
    allowExport,
    receiptGeneration,
    snapshotKey,
    mismatchError = null,
  }, context, asOf, mapStage = { container: null, legend: null, error: null }) {
    if (
      !this.isCurrent()
      || transactionGeneration !== this.snapshotTransactionGeneration
      || operationGeneration !== this.operationGeneration
    ) return false;
    const liveRoot = this.root;
    const stageRoot = document.createElement("div");
    const previousContext = this.currentContext();
    // Pagination is a local view over the fetched user collection. It does not
    // invalidate a full snapshot fetch, but the atomic commit must honor the
    // latest page selected while that fetch was in flight.
    const commitContext = { ...context, page: previousContext.page };
    const publishSnapshot = this.setAnalyticsSnapshot;
    let stagedSnapshot = null;
    this.root = stageRoot;
    this.setAnalyticsSnapshot = (metadata) => {
      stagedSnapshot = metadata ? { ...metadata } : null;
    };
    this.preset = commitContext.preset;
    this.start = commitContext.start || "";
    this.end = commitContext.end || "";
    this.areaKey = commitContext.areaKey;
    this.query = commitContext.query;
    this.activity = commitContext.activity;
    this.sort = commitContext.sort;
    this.page = commitContext.page;
    this.staging = true;
    this.stagedRenderFailed = false;
    try {
      stageRoot.innerHTML = this.shell();
      this.installUserControls();
      this.commitContext(commitContext);
      const [overviewResult, regionsResult, usersResult] = results;
      const resultError = (result, index) => (
        result.status === "fulfilled" && !acceptedIndices.has(index)
          ? mismatchError
          : result.reason
      );
      if (overviewResult.status === "fulfilled" && acceptedIndices.has(0)) {
        this.renderOverviewPayload(overviewResult.value);
      } else if (!isCancellation(resultError(overviewResult, 0))) {
        const error = resultError(overviewResult, 0);
        OVERVIEW_MODULES.forEach((name) => this.fail(name, error));
      }
      if (regionsResult.status === "fulfilled" && acceptedIndices.has(1)) {
        this.renderRegionsModel(regionsResult.value, mapStage);
      } else if (!isCancellation(resultError(regionsResult, 1))) {
        const error = resultError(regionsResult, 1);
        this.fail("map", error);
        this.fail("ranking", error);
        stageRoot.querySelector("[data-summary-total]").textContent = "全体サマリー対象人数を確認できません";
      }
      if (
        !this.isCurrent()
        || transactionGeneration !== this.snapshotTransactionGeneration
        || operationGeneration !== this.operationGeneration
      ) {
        throw new DOMException("overview transaction superseded", "AbortError");
      }
      if (usersResult.status === "fulfilled" && acceptedIndices.has(2)) {
        this.commitUsersModel(usersResult.value, { allowExport });
      } else if (!isCancellation(resultError(usersResult, 2))) {
        const error = resultError(usersResult, 2);
        const target = this.body("users")?.querySelector("#overviewUserResults");
        if (target) target.innerHTML = moduleMessage(error?.message || "ユーザーを取得できませんでした。", "error");
        this.setAnalyticsSnapshot(null);
      }
      if (
        !this.isCurrent()
        || transactionGeneration !== this.snapshotTransactionGeneration
        || operationGeneration !== this.operationGeneration
      ) {
        throw new DOMException("overview transaction superseded", "AbortError");
      }
      this.root = liveRoot;
      this.setAnalyticsSnapshot = publishSnapshot;
      this.staging = false;
      const auxiliaryPanels = ["environment", "usage", "news"]
        .flatMap((name) => this.moduleNames(name))
        .map((name) => ({ name, panel: liveRoot.querySelector(`[data-module="${name}"]`) }))
        .filter(({ panel }) => panel);
      const previouslyCommittedCanvases = [...liveRoot.querySelectorAll("canvas")]
        .filter((canvas) => !auxiliaryPanels.some(({ panel }) => panel.contains(canvas)));
      const restorePopovers = [...this.moduleDateControls.values()]
        .map((control) => control?.preserveOpenPopover());
      liveRoot.replaceChildren(...stageRoot.childNodes);
      // These modules own their requests, dates, and charts independently.
      // Carry their existing panels through the main snapshot commit.
      for (const { name, panel } of auxiliaryPanels) {
        liveRoot.querySelector(`[data-module="${name}"]`).replaceWith(panel);
      }
      restorePopovers.forEach((restore) => restore?.());
      // replaceChildren is the atomic DOM commit point. Only after it succeeds
      // may the prior chart instances be released.
      destroyChartCanvases(previouslyCommittedCanvases);
      if (this.page !== commitContext.page) this.restoreCommittedScope();
      if (allowExport && !this.stagedRenderFailed && stagedSnapshot) publishSnapshot(stagedSnapshot);
      else publishSnapshot(null);
      if (acceptedIndices.size > 0) {
        this.hasCommittedBody = true;
        if (receiptGeneration === "complete" && snapshotKey) {
          this.hasCommittedSnapshot = true;
        }
        this.committedReceiptGeneration = receiptGeneration;
        this.committedAsOf = asOf;
      }
      return true;
    } catch (error) {
      destroyChartsInRoot(stageRoot);
      this.root = liveRoot;
      this.setAnalyticsSnapshot = publishSnapshot;
      this.staging = false;
      this.preset = previousContext.preset;
      this.start = previousContext.start;
      this.end = previousContext.end;
      this.areaKey = previousContext.areaKey;
      this.query = previousContext.query;
      this.activity = previousContext.activity;
      this.sort = previousContext.sort;
      this.page = previousContext.page;
      this.stagedRenderFailed = false;
      throw error;
    }
  }

  renderOverviewPayload({ envelope, models }) {
    const selection = this.root.querySelector("[data-summary-selection]");
    if (this.areaKey) {
      selection.hidden = false;
      selection.textContent = envelope.scopeUserCount == null
        ? "選択地域の対象人数を確認できません"
        : `選択地域 ${envelope.scopeUserCount}名`;
    } else {
      selection.hidden = true;
    }
    const renderModel = (name, result, commit) => {
      if (result.status === "rejected") {
        if (this.hasCommittedBody) throw result.reason;
        this.stagedRenderFailed = true;
        this.fail(name, result.reason);
        return;
      }
      this.renderPart(name, () => commit(result.value));
    };
    renderModel("kpis", models.kpis, (model) => this.renderKpis(model));
    renderModel("activity", models.activity, (model) => this.renderActivity(model));
    renderModel("products", models.products, (model) => this.renderProducts(model));
    if (!this.staging) this.renderAuxiliaryModules();
  }

  renderKpis(model) {
    const activeLabel = this.preset === "today" ? "DAU" : "期間利用者";
    const cards = [
      { label: activeLabel, value: displayCount(model.activeUsers), unit: "人" },
      { label: "利用率", value: displayRate(model.adoptionRate) },
      { label: "再訪率", value: displayRate(model.returnRate), note: model.returnRate == null && this.preset === "today" ? "単日では定義しません" : "2日以上利用した割合" },
      { label: "1人あたり質問", value: model.questionsPerActiveUser == null ? "-" : Number(model.questionsPerActiveUser).toFixed(1), unit: "件" },
      { label: "回答成功率（完全交付）", value: model.completeDelivery ? displayMeasuredRate(model.completeDelivery) : "計測情報なし", measurement: model.completeDelivery },
      { label: "P95応答時間", value: model.p95Latency ? displayMeasuredDuration(model.p95Latency) : "計測情報なし", measurement: model.p95Latency },
    ];
    this.body("kpis").innerHTML = `<div id="kpis" class="kpiGrid">${cards.map((card) => `<article class="kpiCard ${card.measurement ? `measurementCard ${escapeHtml(card.measurement.measurementState)}` : ""}"><span>${escapeHtml(card.label)}</span><div><strong class="${String(card.value).length > 9 ? "textValue" : ""}">${escapeHtml(card.value)}</strong>${card.unit ? `<small>${escapeHtml(card.unit)}</small>` : ""}</div>${card.note ? `<small class="kpiNote">${escapeHtml(card.note)}</small>` : ""}</article>`).join("")}</div>`;
  }

  renderEnvironment(model) {
    const body = this.body("environment");
    const dimension = (measurement, rows, canvasId, missingLabel) => {
      const content = Array.isArray(rows)
        ? `<div class="chartBox compactChart"><canvas id="${canvasId}"></canvas></div>`
        : moduleMessage(`${missingLabel}の内訳を確認できません。`, "error");
      if (measurement?.measurementState === "no_usage") return moduleMessage("この期間の利用記録はありません。", "empty");
      if (measurement?.measurementState === "not_measured") return moduleMessage(`${missingLabel}の記録はありません。`, "empty");
      return content;
    };
    const hourly = Array.isArray(model.hourlyQuestions)
      ? '<div class="chartBox compactChart"><canvas id="hourChart"></canvas></div>'
      : moduleMessage("時間帯別質問を確認できません。", "error");
    body.innerHTML = `<div class="threeGrid"><article class="subPanel"><h4>時間帯別質問</h4>${hourly}</article><article class="subPanel"><h4>デバイス</h4>${dimension(model.deviceMeasurement, model.deviceDistribution, "deviceChart", "デバイス")}</article><article class="subPanel"><h4>利用モード</h4>${dimension(model.modeMeasurement, model.modeDistribution, "modeChart", "利用モード")}</article></div>`;
    if (body.querySelector("#hourChart")) barChart(body.querySelector("#hourChart"), model.hourlyQuestions, { label: "質問数", summary: "時間帯別の質問数" });
    if (body.querySelector("#deviceChart")) doughnutChart(body.querySelector("#deviceChart"), model.deviceDistribution, { summary: "デバイス別の利用構成" });
    if (body.querySelector("#modeChart")) doughnutChart(body.querySelector("#modeChart"), model.modeDistribution, { summary: "利用モード別の構成" });
  }

  renderUsage(model) {
    const body = this.body("usage");
    if (!Array.isArray(model.rows)) { body.innerHTML = moduleMessage("利用推移を確認できません。", "error"); return; }
    body.innerHTML = `<div class="chartBox primaryChart"><canvas id="usageChart"></canvas></div>`;
    usageTrendChart(body.querySelector("#usageChart"), model.rows);
  }

  renderTasks(model) {
    const body = this.body("tasks");
    if (model.measurement?.measurementState === "no_usage") { body.innerHTML = moduleMessage("この期間に利用記録はありません。", "empty"); return; }
    if (model.measurement?.measurementState === "not_measured") { body.innerHTML = moduleMessage("この期間の質問種類の記録はありません。", "empty"); return; }
    const content = Array.isArray(model.rows) ? '<div class="chartBox primaryChart"><canvas id="taskChart"></canvas></div>' : moduleMessage("質問種類の内訳を確認できません。", "error");
    body.innerHTML = content;
    if (body.querySelector("#taskChart")) barChart(body.querySelector("#taskChart"), model.rows, { horizontal: true, label: "質問数", color: "#2fd5c4", summary: "質問種類別の質問数" });
  }

  renderActivity(model) {
    const body = this.body("activity");
    const chartBox = (rows, id, label, className = "") => Array.isArray(rows) ? `<div class="chartBox ${className}"><canvas id="${id}"></canvas></div>` : moduleMessage(`${label}を確認できません。`, "error");
    body.innerHTML = `<div class="activityGrid"><article><h4>全体構成</h4>${chartBox(model.distribution, "activityChart", "全体構成", "donut")}</article><article><h4>地域別構成</h4>${chartBox(model.byArea, "areaActivityChart", "地域別構成")}</article><article><h4>役割別構成</h4>${chartBox(model.byRole, "roleActivityChart", "役割別構成")}</article></div>`;
    if (body.querySelector("#activityChart")) doughnutChart(body.querySelector("#activityChart"), model.distribution, { summary: "全体の活性度構成" });
    if (body.querySelector("#areaActivityChart")) stackedChart(body.querySelector("#areaActivityChart"), model.byArea, { summary: "地域別の活性度構成" });
    if (body.querySelector("#roleActivityChart")) stackedChart(body.querySelector("#roleActivityChart"), model.byRole, { summary: "役割別の活性度構成" });
  }

  renderProducts(model) {
    const body = this.body("products");
    if (model.resolution?.measurementState === "no_usage") { body.innerHTML = moduleMessage("この期間に利用記録はありません。", "empty"); return; }
    if (model.resolution?.measurementState === "not_measured") { body.innerHTML = moduleMessage("この期間の製品情報の記録はありません。", "empty"); return; }
    const ranking = Array.isArray(model.topProducts) ? '<div class="chartBox primaryChart"><canvas id="productChart"></canvas></div>' : moduleMessage("製品ランキングを確認できません。", "error");
    const matrix = Array.isArray(model.matrix) ? '<div id="productMatrix" class="productMatrix"></div>' : moduleMessage("製品マトリクスを確認できません。", "error");
    body.innerHTML = `<div class="twoGrid productGrid"><article class="subPanel"><h4>質問対象（製品）Top 10</h4>${ranking}</article><article class="subPanel matrixPanel"><h4>製品 × 質問種類</h4>${matrix}</article></div>`;
    if (body.querySelector("#productChart")) barChart(body.querySelector("#productChart"), model.topProducts, { horizontal: true, label: "質問数", color: "#7f88ff", summary: "質問対象製品の上位10件" });
    if (body.querySelector("#productMatrix")) renderProductMatrix(body.querySelector("#productMatrix"), model.matrix);
  }

  renderRegionsModel(model, mapStage) {
    this.root.querySelector("[data-summary-total]").textContent = model.scopeUserCount == null
      ? "全体サマリー対象人数を確認できません"
      : `全体サマリー対象 ${model.scopeUserCount}名`;
    this.renderRanking(model.regions, model.issues, model.contentDiagnostics?.notice || "");
    if (mapStage.error) this.fail("map", mapStage.error);
    else if (mapStage.container && mapStage.legend) {
      this.body("map").replaceChildren(mapStage.container, mapStage.legend);
    }
    this.renderAreaChip(model.regions);
  }

  renderRanking(rows, issues = [], diagnosticsNotice = "") {
    const body = this.body("ranking");
    body.innerHTML = `<div id="regionRanking" class="rankingList">${rows.map((row, index) => `<button class="rankingRow ${row.areaKey === this.areaKey ? "isSelected" : ""}" data-area="${escapeHtml(row.areaKey)}" aria-pressed="${row.areaKey === this.areaKey}"><b>${index + 1}</b><span class="rankingLabel"><strong>${escapeHtml(row.area)}</strong><small>${displayCount(row.activeUsers)} / ${displayCount(row.rosterUsers)}人 · ${displayCount(row.questions)}件</small></span><span class="rankingValue">${displayRate(row.adoptionRate)}</span><i class="rankingBar" style="--bar:${Math.max(0, Math.min(100, Number(row.adoptionRate || 0) * 100))}%"></i></button>`).join("") || moduleMessage("この期間に地域利用はありません。", "empty")}</div>`;
    body.querySelectorAll(".rankingRow").forEach((button) => button.addEventListener("click", () => this.setArea(button.dataset.area === this.areaKey ? "" : button.dataset.area)));
  }

  renderAreaChip(rows) {
    const selected = rows.find((row) => row.areaKey === this.areaKey);
    this.root.querySelector("#areaChip").innerHTML = selected ? `<button class="filterChip" id="clearArea" aria-label="${escapeHtml(selected.area)}の絞り込みを解除">${escapeHtml(selected.area)} <span aria-hidden="true">×</span></button>` : "";
    this.root.querySelector("#clearArea")?.addEventListener("click", () => this.setArea(""));
  }

  installUserControls() {
    const body = this.body("users");
    body.innerHTML = `<div class="collectionToolbar"><label>ユーザー検索<input id="overviewUserSearch" type="search" value="${escapeHtml(this.query)}" placeholder="氏名またはメール"></label><label>活性度<select id="overviewActivity"><option value="">すべて</option><option value="high" ${this.activity === "high" ? "selected" : ""}>高</option><option value="middle" ${this.activity === "middle" ? "selected" : ""}>中</option><option value="low" ${this.activity === "low" ? "selected" : ""}>低</option><option value="dormant" ${this.activity === "dormant" ? "selected" : ""}>休眠</option></select></label><label>並び順<select id="overviewSort"><option value="last_desc" ${this.sort === "last_desc" ? "selected" : ""}>最終利用が新しい順</option><option value="name_asc" ${this.sort === "name_asc" ? "selected" : ""}>社員名順</option><option value="messages_desc" ${this.sort === "messages_desc" ? "selected" : ""}>メッセージ数順</option><option value="success_desc" ${this.sort === "success_desc" ? "selected" : ""}>回答成功率順</option></select></label></div><div id="overviewUserResults">${moduleMessage("読み込み中…", "loading")}</div>`;
    body.querySelector("#overviewUserSearch").addEventListener("input", (event) => {
      this.updateUserCollection({ query: event.target.value, page: 1 });
      this.setAnalyticsSnapshot(null);
      window.clearTimeout(this.userSearchTimer);
      this.userSearchTimer = window.setTimeout(() => this.refreshUsers(), 250);
    });
    body.querySelector("#overviewActivity").addEventListener("change", (event) => {
      this.updateUserCollection({ activity: event.target.value, page: 1 });
      this.refreshUsers();
    });
    body.querySelector("#overviewSort").addEventListener("change", (event) => {
      this.updateUserCollection({ sort: event.target.value, page: 1 });
      this.refreshUsers();
    });
  }

  async refreshUsers() {
    const generation = ++this.operationGeneration;
    this.setAnalyticsSnapshot(null);
    const committedAsOfMs = Date.parse(this.committedAsOf);
    if (
      !Number.isFinite(committedAsOfMs)
      || Math.abs(Date.now() - committedAsOfMs) >= MAX_COMMITTED_ANCHOR_AGE_MS
    ) {
      await this.runSnapshotTransaction(this.currentContext());
      return;
    }
    try {
      const raw = await getOverviewUsers({
        ...requestDateRange(this.currentContext(), this.committedAsOf),
        area_key: this.areaKey,
        q: this.query,
        activity: this.activity,
        sort: this.sort,
      }, { signal: this.signal });
      if (!this.isCurrent() || generation !== this.operationGeneration) return;
      const model = usersModel(raw, "global");
      const incomingReceiptGeneration = model.scopeMetadata.available ? "complete" : "legacy";
      const receiptGenerationChanged = this.committedReceiptGeneration !== "none"
        && incomingReceiptGeneration !== this.committedReceiptGeneration;
      const completeKeyChanged = incomingReceiptGeneration === "complete"
        && this.committedReceiptGeneration === "complete"
        && model.scopeMetadata.snapshotKey !== this.snapshotKey;
      if (this.snapshotMismatch || receiptGenerationChanged || completeKeyChanged) {
        await this.runSnapshotTransaction(this.currentContext());
        return;
      }
      this.commitUsersModel(model);
    } catch (error) {
      if (isCancellation(error)) throw error;
      if (generation !== this.operationGeneration) return;
      const target = this.body("users")?.querySelector("#overviewUserResults");
      if (this.userModel) {
        this.userRefreshError = "ユーザー一覧を更新できませんでした。表示中の内容を保持しています。";
        this.renderUserRows();
      } else if (target) {
        target.innerHTML = moduleMessage(error?.message || "ユーザーを取得できませんでした。", "error");
      }
      this.setAnalyticsSnapshot(null);
    }
  }

  commitUsersModel(model, { allowExport = true } = {}) {
    if (
      allowExport
      && model.scopeMetadata.available
      && model.contentDiagnostics?.exportAvailable === true
    ) this.setAnalyticsSnapshot(model.scopeMetadata);
    else this.setAnalyticsSnapshot(null);
    this.userRefreshError = "";
    this.userModel = model;
    this.renderUserRows();
  }

  updateUserCollection({ query = this.query, activity = this.activity, sort = this.sort, page = this.page }) {
    const serverFilterChanged = query !== this.query
      || activity !== this.activity
      || sort !== this.sort;
    if (serverFilterChanged) ++this.operationGeneration;
    this.query = query;
    this.activity = activity;
    this.sort = sort;
    this.page = page;
    this.navigate("overview", { overviewQuery: query, overviewActivity: activity, overviewSort: sort, overviewPage: page }, { replace: true, render: false });
    this.renderUserRows();
  }

  renderUserRows() {
    const target = this.body("users")?.querySelector("#overviewUserResults");
    if (!target || !this.userModel) return;
    const rows = [...this.userModel.users];
    const page = paginate(rows, this.page, this.compactCollection.matches ? 6 : 15);
    if (page.page !== this.page) {
      this.page = page.page;
      if (!this.staging) this.navigate("overview", { overviewPage: this.page }, { replace: true, render: false });
    }
    const rowHtml = page.items.map((row) => `<tr><td><strong>${escapeHtml(row.name)}</strong>${row.labels.length ? `<div class="chips">${chips(row.labels)}</div>` : ""}<small>${escapeHtml(row.role)}</small></td><td>${escapeHtml(row.email)}</td><td>${escapeHtml(row.area)}</td><td>${displayDateTime(row.lastActiveAt)}</td><td>${displayCount(row.activeDaysInPeriod)}</td><td>${displayCount(row.userMessageCountInPeriod)}</td><td>${displayMeasuredRate(row.completeDelivery)}</td><td><span class="activityBadge ${escapeHtml(row.activity || "unknown")}">${escapeHtml(row.activityLabel)}</span></td><td><button class="linkButton" data-roster="${escapeHtml(row.rosterId)}">詳細</button></td></tr>`).join("");
    const cardHtml = page.items.map((row) => `<article class="userCard"><header><div><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)}</small><small>${escapeHtml(row.role)}・${escapeHtml(row.department)}</small>${row.labels.length ? `<div class="chips">${chips(row.labels)}</div>` : ""}</div><span class="activityBadge ${escapeHtml(row.activity || "unknown")}">${escapeHtml(row.activityLabel)}</span></header><dl><div><dt>エリア</dt><dd>${escapeHtml(row.area)}</dd></div><div><dt>最終利用</dt><dd>${displayDateTime(row.lastActiveAt)}</dd></div><div><dt>期間内利用日数</dt><dd>${displayCount(row.activeDaysInPeriod)}日</dd></div><div><dt>期間内質問数</dt><dd>${displayCount(row.userMessageCountInPeriod)}件</dd></div><div><dt>完全交付</dt><dd>${displayMeasuredRate(row.completeDelivery)}</dd></div></dl><button class="linkButton" data-roster="${escapeHtml(row.rosterId)}">詳細を見る</button></article>`).join("");
    const refreshErrorNote = this.userRefreshError
      ? `<p class="measurementNote" data-user-refresh-error role="alert">${escapeHtml(this.userRefreshError)}</p>`
      : "";
    target.innerHTML = (page.total ? `<div class="desktopTable"><div class="tableScroll" tabindex="0" aria-label="全体サマリーユーザー一覧"><table id="overviewUsers"><caption>本社MR・コントラクトMRの利用状況</caption><thead><tr><th>社員名・役割</th><th>メール</th><th>エリア</th><th>最終利用</th><th>期間内利用日数</th><th>期間内質問数</th><th>回答成功率</th><th>活性度</th><th></th></tr></thead><tbody>${rowHtml}</tbody></table></div></div><div class="mobileCards">${cardHtml}</div>${paginationMarkup(page)}` : moduleMessage("条件に一致するユーザーはいません。", "empty")) + refreshErrorNote;
    target.querySelectorAll("[data-roster]").forEach((button) => button.addEventListener("click", () => this.navigate("user", { roster: button.dataset.roster })));
    bindPagination(target, page, (next) => this.updateUserCollection({ page: next }));
  }
}
