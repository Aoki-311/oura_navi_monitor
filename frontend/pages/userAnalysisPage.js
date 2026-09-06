import {
  getTraceMessages, getUserConversations, getUserDetail, getUsers, getNewsUsageUser, isCancellation,
  timeRangeQuery,
} from "../api/client.js";
import {
  conversationsModel, messageModel, userComparisonsModel, userDetailEnvelope,
  userNeedsModel, userProfileModel, userSummaryModel, userTrendModel, usersModel,
} from "../adapters/usersAdapter.js";
import { barChart, destroyChartCanvases, destroyChartsInRoot, doughnutChart, trendChart } from "../components/charts.js";
import { bindPagination, bindResponsiveCollection, paginate, paginationMarkup } from "../components/collection.js";
import { chips, compareJapaneseNames, escapeHtml, moduleMessage, setBusy } from "../components/dom.js";
import { normalizeDateRange, requestDateRange } from "../components/dateRangeControl.js";
import { newsUsageDashboardModel } from "../adapters/newsUsageDashboardAdapter.js";
import { renderFreshnessBanner } from "../components/freshnessBanner.js";
import { displayCount, displayDateTime, displayMeasuredDuration, displayMeasuredRate } from "../viewModels/formatters.js";

const DETAIL_MODULES = ["profile", "summary", "trend", "needs"];
const MAX_ANALYSIS_ANCHOR_AGE_MS = 4 * 60 * 1000;

function capturedModel(create) {
  try { return { status: "fulfilled", value: create() }; }
  catch (reason) { return { status: "rejected", reason }; }
}

export class UserAnalysisPage {
  constructor(root, {
    rosterId = "", navigate, toast, getPreset, state, signal, isCurrent,
    setAnalyticsSnapshot, analyticsAsOf = "",
  }) {
    this.root = root;
    this.rosterId = rosterId;
    this.navigate = navigate;
    this.toast = toast;
    this.getPreset = getPreset;
    this.signal = signal;
    this.isCurrent = isCurrent;
    this.setAnalyticsSnapshot = setAnalyticsSnapshot;
    this.preset = state.preset;
    this.start = state.start;
    this.end = state.end;
    this.chooserScrollTop = 0;
    this.newsRequest = null;
    this.analysisAsOf = analyticsAsOf || new Date().toISOString();
    this.userQuery = state.userQuery;
    this.userPage = state.userPage;
    this.chooserUsers = [];
    this.selectedConversation = "";
    this.messageCursor = "";
    this.messageRequest = null;
    this.messageAbortCleanup = null;
    this.messageGeneration = 0;
    this.messageLoading = false;
    this.conversationRequest = null;
    this.conversationAbortCleanup = null;
    this.conversationGeneration = 0;
    this.operationGeneration = 0;
    this.chooserViewRevision = 0;
    this.hasCommittedView = false;
    this.staging = false;
    this.compactCollection = bindResponsiveCollection(this.signal, () => {
      if (!this.rosterId && this.root.querySelector("#userChoices")) this.renderChooserRows();
    });
  }

  async load() {
    return this.transition({
      preset: this.preset,
      start: this.start,
      end: this.end,
      roster: this.rosterId,
      userQuery: this.userQuery,
      userPage: this.userPage,
    }, { initial: true });
  }

  requiresAnchorRefresh() {
    const anchorMs = Date.parse(this.analysisAsOf);
    return !Number.isFinite(anchorMs)
      || Math.abs(Date.now() - anchorMs) >= MAX_ANALYSIS_ANCHOR_AGE_MS;
  }

  renderAnchorRefreshIssue() {
    const banner = this.root.querySelector("[data-freshness-banner]");
    if (!banner) return;
    banner.hidden = false;
    let notice = banner.querySelector("[data-user-anchor-error]");
    if (!notice) {
      notice = document.createElement("span");
      notice.dataset.userAnchorError = "true";
      notice.setAttribute("role", "alert");
      banner.appendChild(notice);
    }
    notice.textContent = "分析期間を更新できませんでした。表示中の内容を保持しています。";
  }

  restoreCommittedRoute(rosterId = this.rosterId) {
    this.navigate("user", {
      preset: this.preset,
      start: this.start,
      end: this.end,
      roster: rosterId,
      userQuery: this.userQuery,
      userPage: this.userPage,
    }, { replace: true, render: false });
  }

  routeState() {
    return {
      preset: this.preset,
      start: this.start,
      end: this.end,
      roster: this.rosterId,
      userQuery: this.userQuery,
      userPage: this.userPage,
    };
  }

  async refreshExpiredAnchor(state) {
    return this.transition(state, { forceAnchor: true });
  }

  async transition(state, { forceAnchor = false, initial = false } = {}) {
    const generation = ++this.operationGeneration;
    const chooserViewRevision = this.chooserViewRevision;
    const liveRoot = this.root;
    const previous = {
      preset: this.preset,
      start: this.start,
      end: this.end,
      rosterId: this.rosterId,
      analysisAsOf: this.analysisAsOf,
      userQuery: this.userQuery,
      userPage: this.userPage,
      chooserUsers: this.chooserUsers,
    };
    const nextPreset = state.preset || "last_7d";
    const nextRange = normalizeDateRange({ preset: nextPreset, start: state.start, end: state.end });
    const nextRosterId = state.roster || "";
    const shouldRefreshAnchor = forceAnchor || nextPreset !== this.preset || nextRange.start !== this.start || nextRange.end !== this.end || this.requiresAnchorRefresh();
    const nextAsOf = shouldRefreshAnchor ? new Date().toISOString() : this.analysisAsOf;
    const nextQuery = state.userQuery || "";
    const nextPage = Math.max(1, Number(state.userPage) || 1);
    const rosterChanging = nextRosterId !== previous.rosterId;
    let liveChooserContext = null;
    if (rosterChanging) this.cancelConversationRequest();
    if (initial) liveRoot.innerHTML = nextRosterId ? this.shell() : "";
    if (initial && !nextRosterId) this.renderChooserShell();
    if (rosterChanging && nextRosterId && !previous.rosterId) {
      this.chooserScrollTop = window.scrollY;
      const selected = [...liveRoot.querySelectorAll(".userChoice")].find((button) => button.dataset.roster === nextRosterId);
      selected?.classList.add("isSelected");
      selected?.setAttribute("aria-busy", "true");
      liveRoot.querySelector(".pageHeading")?.insertAdjacentHTML("afterend", `<section class="panel pendingUserDetail" data-pending-user>${moduleMessage("ユーザー分析を読み込んでいます…", "loading")}</section>`);
    }
    setBusy(liveRoot, true);
    this.setAnalyticsSnapshot(null);
    try {
      const [chooserResult, detailResult] = await Promise.allSettled([
        this.fetchChooserModel(nextAsOf, nextRange),
        nextRosterId ? this.fetchDetailPayload(nextRosterId, nextAsOf, nextRange) : Promise.resolve(null),
      ]);
      if (!this.isCurrent() || generation !== this.operationGeneration) return false;
      // Search and pagination are a local view over the fetched roster. They
      // must survive a full chooser refresh, but only while the server-owned
      // roster target is still the same chooser transaction. A newer roster or
      // preset transition increments operationGeneration and cannot be merged.
      if (
        !previous.rosterId
        && !nextRosterId
        && !this.rosterId
        && this.chooserViewRevision !== chooserViewRevision
      ) {
        liveChooserContext = { query: this.userQuery, page: this.userPage };
      }
      if (!initial && chooserResult.status === "rejected") throw chooserResult.reason;
      if (!initial && nextRosterId && detailResult.status === "rejected") throw detailResult.reason;
      if (
        !initial
        && this.hasCommittedView
        && nextRosterId
        && detailResult.status === "fulfilled"
        && ["profile", "summary", "trend", "needs"]
          .some((name) => detailResult.value[name]?.status === "rejected")
      ) {
        const failed = ["profile", "summary", "trend", "needs"]
          .map((name) => detailResult.value[name])
          .find((result) => result?.status === "rejected");
        throw failed.reason;
      }
      let committedRosterId = nextRosterId;
      if (
        committedRosterId
        && chooserResult.status === "fulfilled"
        && !chooserResult.value.users.some((row) => row.rosterId === committedRosterId)
      ) {
        throw new Error("対象ユーザーを新しい分析期間で確認できませんでした。");
      }
      if (
        initial
        && committedRosterId
        && detailResult.status === "rejected"
        && (detailResult.reason?.status === 404 || detailResult.reason?.code === "user_not_found")
        && chooserResult.status === "fulfilled"
      ) {
        committedRosterId = "";
      }

      const stageRoot = document.createElement("div");
      const commitChooserContext = liveChooserContext || { query: nextQuery, page: nextPage };
      const publishSnapshot = this.setAnalyticsSnapshot;
      let stagedSnapshot = null;
      let chooserRouteNeedsSync = false;
      let carriedConversation = false;
      let carriedConversationPanel = null;
      let carriedConversationParent = null;
      let carriedConversationNextSibling = null;
      this.root = stageRoot;
      this.setAnalyticsSnapshot = (metadata) => { stagedSnapshot = metadata ? { ...metadata } : null; };
      this.preset = nextPreset;
      this.start = nextRange.start;
      this.end = nextRange.end;
      this.analysisAsOf = nextAsOf;
      this.userQuery = commitChooserContext.query;
      this.userPage = commitChooserContext.page;
      this.rosterId = committedRosterId;
      this.staging = true;
      try {
        if (!committedRosterId) {
          this.renderChooserShell();
          if (chooserResult.status === "fulfilled") this.commitChooserModel(chooserResult.value);
          else {
            const error = chooserResult.reason;
            renderFreshnessBanner(stageRoot.querySelector("[data-freshness-banner]"), null, null, [error?.message || "ユーザーを取得できません。"]) ;
            stageRoot.querySelector("#userChoices").innerHTML = moduleMessage(error?.message || "ユーザーを取得できません。", "error");
          }
        } else {
          stageRoot.innerHTML = this.shell();
          stageRoot.querySelector("#changeUser").addEventListener("click", () => this.navigate("user", { roster: "" }));
          // The first usable body is assembled module by module. A local
          // chart/DOM failure must fail only that module and must not replace
          // the valid profile, summary and sibling needs with the chooser.
          // Once a complete view exists, strict mode keeps the previously
          // committed transaction intact on a failed refresh.
          if (detailResult.status === "fulfilled") {
            this.commitDetailPayload(detailResult.value, { strictRender: this.hasCommittedView });
          }
          else {
            const error = detailResult.reason;
            DETAIL_MODULES.forEach((name) => this.fail(name, error));
            renderFreshnessBanner(stageRoot.querySelector("[data-freshness-banner]"), null, null, [error?.message || "個人分析を取得できません。"]) ;
          }
        }
        if (!this.isCurrent() || generation !== this.operationGeneration) {
          throw new DOMException("user analysis transaction superseded", "AbortError");
        }
        chooserRouteNeedsSync = !committedRosterId && this.userPage !== commitChooserContext.page;
        if (
          committedRosterId
          && committedRosterId === previous.rosterId
          && this.hasCommittedView
        ) {
          const previousPanel = liveRoot.querySelector('[data-module="conversations"]');
          const stagedPanel = stageRoot.querySelector('[data-module="conversations"]');
          if (previousPanel?.querySelector(".conversationJourney") && stagedPanel) {
            carriedConversation = true;
            carriedConversationPanel = previousPanel;
            carriedConversationParent = previousPanel.parentNode;
            carriedConversationNextSibling = previousPanel.nextSibling;
            stagedPanel.replaceWith(previousPanel);
          }
        }
        this.root = liveRoot;
        this.setAnalyticsSnapshot = publishSnapshot;
        this.staging = false;
        if (!carriedConversation) this.messageRequest?.abort();
        const previouslyCommittedCanvases = [...liveRoot.querySelectorAll("canvas")];
        if (committedRosterId && committedRosterId === previous.rosterId) {
          const previousNews = liveRoot.querySelector('[data-module="news"]');
          if (previousNews?.dataset.loaded === "true") stageRoot.querySelector('[data-module="news"]')?.replaceWith(previousNews);
        }
        liveRoot.replaceChildren(...stageRoot.childNodes);
        destroyChartCanvases(previouslyCommittedCanvases);
        publishSnapshot(stagedSnapshot);
        this.hasCommittedView = true;
        // Staging may clamp a now-invalid page after a roster shrinks. URL
        // normalization is a post-commit side effect: never expose the staged
        // route before the new chooser DOM has committed successfully.
        if (chooserRouteNeedsSync) this.restoreCommittedRoute("");
      } catch (error) {
        if (carriedConversationPanel && carriedConversationParent && !liveRoot.contains(carriedConversationPanel)) {
          if (carriedConversationNextSibling?.parentNode === carriedConversationParent) {
            carriedConversationParent.insertBefore(carriedConversationPanel, carriedConversationNextSibling);
          } else {
            carriedConversationParent.appendChild(carriedConversationPanel);
          }
        }
        destroyChartsInRoot(stageRoot);
        this.root = liveRoot;
        this.setAnalyticsSnapshot = publishSnapshot;
        this.staging = false;
        throw error;
      }

      if (
        initial
        && nextRosterId
        && !committedRosterId
        && detailResult.status === "rejected"
      ) {
        this.toast("対象ユーザーは停用済み、または分析対象外です。ユーザー管理から確認してください。", "error");
        this.restoreCommittedRoute("");
      }
      if (committedRosterId && !carriedConversation) void this.loadConversations().catch(() => {});
      if (committedRosterId) void this.loadNewsUsage(committedRosterId, nextRange, nextAsOf);
      else if (previous.rosterId) window.requestAnimationFrame(() => window.scrollTo({ top: this.chooserScrollTop, behavior: "instant" }));
      return true;
    } catch (error) {
      if (isCancellation(error)) throw error;
      if (!this.isCurrent() || generation !== this.operationGeneration) return false;
      if (
        !liveChooserContext
        && !previous.rosterId
        && !nextRosterId
        && !this.rosterId
        && this.chooserViewRevision !== chooserViewRevision
      ) {
        liveChooserContext = { query: this.userQuery, page: this.userPage };
      }
      this.root = liveRoot;
      this.preset = previous.preset;
      this.start = previous.start;
      this.end = previous.end;
      this.rosterId = previous.rosterId;
      this.analysisAsOf = previous.analysisAsOf;
      this.userQuery = liveChooserContext?.query ?? previous.userQuery;
      this.userPage = liveChooserContext?.page ?? previous.userPage;
      this.chooserUsers = previous.chooserUsers;
      if (this.hasCommittedView) {
        this.restoreCommittedRoute();
        this.renderAnchorRefreshIssue();
        if (
          rosterChanging
          && previous.rosterId
          && !liveRoot.querySelector('[data-module="conversations"] .conversationJourney')
        ) void this.loadConversations(previous.rosterId).catch(() => {});
      } else {
        this.renderChooserShell();
        renderFreshnessBanner(liveRoot.querySelector("[data-freshness-banner]"), null, null, [error?.message || "ユーザー分析を取得できません。"]) ;
        liveRoot.querySelector("#userChoices").innerHTML = moduleMessage(error?.message || "ユーザー分析を取得できません。", "error");
      }
      this.setAnalyticsSnapshot(null);
      return false;
    } finally {
      if (this.isCurrent() && generation === this.operationGeneration) {
        setBusy(liveRoot, false);
        liveRoot.querySelector("[data-pending-user]")?.remove();
        liveRoot.querySelectorAll(".userChoice.isSelected").forEach((button) => { button.classList.remove("isSelected"); button.removeAttribute("aria-busy"); });
      }
    }
  }

  shell() {
    return `<div class="pageHeading userAnalysisHeading"><div><h2>ユーザー分析</h2></div><button class="ghostButton" id="changeUser">ユーザー一覧に戻る</button></div>
      <div class="freshnessBanner" data-freshness-banner data-state="loading">更新状況を確認中です。</div>
      <section class="panel" data-module="profile"><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
      <section class="panel" data-module="summary"><div class="panelHead"><h3><span>01</span>個人利用サマリー</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div><div class="personalNewsModule" data-module="news" aria-label="ニュース・学会の利用"><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></div></section>
      <section class="panel" data-module="trend"><div class="panelHead"><h3><span>02</span>個人利用推移</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
      <section class="panel" data-module="needs"><div class="panelHead"><h3><span>03</span>ユーザーニーズ傾向</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
      <section class="panel" data-module="conversations"><div class="panelHead"><h3><span>04</span>会話ジャーニー</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>`;
  }

  body(name) { return this.root.querySelector(`[data-module="${name}"] [data-module-body]`); }

  fail(name, error) {
    if (!this.isCurrent() || isCancellation(error)) return;
    const target = this.body(name);
    if (target) target.innerHTML = moduleMessage(error?.message || "データを取得できませんでした。", "error");
  }

  renderChooserShell() {
    this.root.innerHTML = `<div class="pageHeading"><div><h2>ユーザー分析</h2><p>分析するユーザーを選択</p></div></div><div class="freshnessBanner" data-freshness-banner data-state="loading">更新状況を確認中です。</div><section class="panel chooserPanel"><div class="collectionToolbar"><label>ユーザー検索<input id="userSearch" type="search" value="${escapeHtml(this.userQuery)}" placeholder="氏名・メール・役割・ラベル"></label><span class="chooserSortLabel">氏名順</span></div><div id="userChoices" class="userChoices">${moduleMessage("読み込み中…", "loading")}</div><div id="userChooserPagination"></div></section>`;
  }

  async fetchChooserModel(asOf = this.analysisAsOf, preset = { preset: this.preset, start: this.start, end: this.end }) {
    return usersModel(await getUsers(
      timeRangeQuery(preset, asOf),
      { signal: this.signal },
    ));
  }

  commitChooserModel(model) {
    renderFreshnessBanner(
      this.root.querySelector("[data-freshness-banner]"),
      model.freshness,
      null,
      model.metadataIssues,
    );
    this.chooserUsers = [...model.users].sort((a, b) => compareJapaneseNames(a.name, b.name) || compareJapaneseNames(a.email, b.email));
    this.renderChooserRows();
    this.root.querySelector("#userSearch").addEventListener("input", (event) => {
      this.chooserViewRevision += 1;
      this.userQuery = event.target.value;
      this.userPage = 1;
      this.navigate("user", { roster: "", userQuery: this.userQuery, userPage: 1 }, { replace: true, render: false });
      this.renderChooserRows();
    });
  }

  renderChooserRows() {
    const query = this.userQuery.normalize("NFKC").trim().toLocaleLowerCase("ja-JP");
    const rows = this.chooserUsers.filter((row) => {
      const labelText = row.labels.map((label) => label.name).join(" ");
      const haystack = `${row.name} ${row.email} ${row.role} ${row.department} ${labelText}`.normalize("NFKC").toLocaleLowerCase("ja-JP");
      return !query || haystack.includes(query);
    });
    const page = paginate(rows, this.userPage, this.compactCollection.matches ? 8 : 15);
    if (page.page !== this.userPage) {
      this.userPage = page.page;
      if (!this.staging) {
        this.chooserViewRevision += 1;
        this.navigate("user", { roster: "", userQuery: this.userQuery, userPage: page.page }, { replace: true, render: false });
      }
    }
    const choices = this.root.querySelector("#userChoices");
    const pagination = this.root.querySelector("#userChooserPagination");
    choices.innerHTML = page.items.map((row) => `<button class="userChoice" data-roster="${escapeHtml(row.rosterId)}"><span class="avatar">${escapeHtml(row.name.slice(0, 1))}</span><span><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)} · ${escapeHtml(row.area)}</small><small>${escapeHtml(row.role)} · ${escapeHtml(row.department)}</small>${row.labels.length ? `<span class="chips">${chips(row.labels)}</span>` : ""}</span><em class="activityBadge ${escapeHtml(row.activity || "unknown")}">${escapeHtml(row.activityLabel)}</em></button>`).join("") || moduleMessage("条件に一致するユーザーはいません。", "empty");
    pagination.innerHTML = page.total ? paginationMarkup(page) : "";
    choices.querySelectorAll(".userChoice").forEach((button) => button.addEventListener("click", () => this.navigate("user", { roster: button.dataset.roster })));
    bindPagination(pagination, page, (next) => {
      this.chooserViewRevision += 1;
      this.userPage = next;
      this.navigate("user", { roster: "", userQuery: this.userQuery, userPage: next }, { replace: true, render: false });
      this.renderChooserRows();
    });
  }

  prepareDetailPayload(raw) {
    return {
      envelope: userDetailEnvelope(raw),
      profile: capturedModel(() => userProfileModel(raw)),
      summary: capturedModel(() => ({
        summary: userSummaryModel(raw),
        comparisons: userComparisonsModel(raw),
      })),
      trend: capturedModel(() => userTrendModel(raw)),
      needs: capturedModel(() => userNeedsModel(raw)),
    };
  }

  async fetchDetailPayload(rosterId = this.rosterId, asOf = this.analysisAsOf, preset = { preset: this.preset, start: this.start, end: this.end }) {
    const raw = await getUserDetail(
      rosterId,
      timeRangeQuery(preset, asOf),
      { signal: this.signal },
    );
    return this.prepareDetailPayload(raw);
  }

  commitDetailPayload(payload, { strictRender = false } = {}) {
    const { envelope } = payload;
    if (envelope.scopeMetadata.available && envelope.contentDiagnostics.exportAvailable) {
      this.setAnalyticsSnapshot(envelope.scopeMetadata);
    } else {
      this.setAnalyticsSnapshot(null);
    }
    renderFreshnessBanner(
      this.root.querySelector("[data-freshness-banner]"),
      envelope.freshness,
      envelope.analyticsQuality,
      envelope.metadataIssues,
    );
    const render = (name, result, commit) => {
      if (result.status === "rejected") {
        this.fail(name, result.reason);
        return;
      }
      try { commit(result.value); } catch (error) {
        if (strictRender) throw error;
        this.fail(name, error);
      }
    };
    render("profile", payload.profile, (profile) => this.renderProfile(profile, envelope.contentDiagnostics));
    render("summary", payload.summary, ({ summary, comparisons }) => this.renderSummary(summary, comparisons));
    render("trend", payload.trend, (trend) => this.renderTrend(trend, { strictRender }));
    render("needs", payload.needs, (needs) => this.renderNeeds(needs, { strictRender }));
  }

  async loadDetail() {
    try {
      const payload = await this.fetchDetailPayload();
      if (!this.isCurrent()) return;
      this.commitDetailPayload(payload);
    } catch (error) {
      if (isCancellation(error)) throw error;
      if (error?.status === 404 || error?.code === "user_not_found") {
        this.toast("対象ユーザーは停用済み、または分析対象外です。ユーザー管理から確認してください。", "error");
        this.navigate("user", { roster: "" }, { replace: true });
        return;
      }
      DETAIL_MODULES.forEach((name) => this.fail(name, error));
      renderFreshnessBanner(this.root.querySelector("[data-freshness-banner]"), null, null, [error?.message || "個人分析を取得できません。"]) ;
    }
  }

  async loadNewsUsage(rosterId, range, asOf) {
    this.newsRequest?.abort();
    const controller = new AbortController();
    this.newsRequest = controller;
    const cancel = () => controller.abort();
    this.signal.addEventListener("abort", cancel, { once: true });
    const panel = this.root.querySelector('[data-module="news"]');
    const target = panel?.querySelector("[data-module-body]");
    if (!target) return;
    const current = () => this.isCurrent() && !controller.signal.aborted && this.rosterId === rosterId && panel.isConnected;
    setBusy(panel, true);
    panel.querySelector("[data-news-error]")?.remove();
    try {
      const model = newsUsageDashboardModel(await getNewsUsageUser(rosterId, requestDateRange(range, asOf), { signal: controller.signal }), { scope: "user_map", rosterId });
      if (!current()) return;
      if (!model.available) {
        const messages = { not_enabled: "利用状況の計測はまだ始まっていません。", before_measurement: "選択した期間は計測開始前です。", unavailable: "利用状況を取得できませんでした。" };
        target.innerHTML = moduleMessage(messages[model.state.availability], model.state.availability === "unavailable" ? "error" : "empty");
        panel.dataset.loaded = "false";
        return;
      }
      const values = model.totals;
      const breakdown = (rows) => rows.map(([label, count]) => `<span><span>${escapeHtml(label)}</span><b>${displayCount(count)}回</b></span>`).join("");
      const card = (key, label, count, rows) => `<article class="newsKpiCard" tabindex="0" data-news-kpi="${key}" aria-label="${escapeHtml(label)} ${displayCount(count)}回"><span>${escapeHtml(label)}</span><strong>${displayCount(count)}<small>回</small></strong><span class="newsKpiHint">内訳</span><div class="newsHoverDetails" role="tooltip">${breakdown(rows)}</div></article>`;
      target.innerHTML = `<div class="newsKpiGrid">${card("tabViews", "ニュース・学会の訪問", values.tabViews, [["ニュース", values.newsTabViews], ["学会情報", values.societyTabViews]])}${card("newsContentClicks", "ニュースの閲覧・原文クリック", values.newsContentClicks, [["国内", values.newsDomesticClicks], ["海外", values.newsOverseasClicks], ...(values.newsUnknownGeographyClicks > 0 ? [["未分類", values.newsUnknownGeographyClicks]] : [])])}${card("societyContentClicks", "学会情報の閲覧・原文クリック", values.societyContentClicks, model.societyCategories.map((row) => [row.label, row.clicks]))}</div>`;
      if (model.state.freshness === "stale" || model.state.freshnessState === "stale") target.insertAdjacentHTML("beforeend", moduleMessage("更新が遅れています。直近の操作はまだ含まれていない場合があります。"));
      panel.dataset.loaded = "true";
    } catch (error) {
      if (!current() || isCancellation(error)) return;
      if (panel.dataset.loaded === "true") target.insertAdjacentHTML("beforeend", `<div data-news-error>${moduleMessage("更新できませんでした。前回の表示を保持しています。", "error")}</div>`);
      else target.innerHTML = moduleMessage("ニュース・学会の利用状況を取得できませんでした。", "error");
    } finally {
      this.signal.removeEventListener("abort", cancel);
      if (current()) setBusy(panel, false);
    }
  }

  renderProfile(profile, contentDiagnostics = null) {
    const body = this.body("profile");
    const labelNotice = contentDiagnostics?.notice
      ? moduleMessage(contentDiagnostics.notice, "error")
      : "";
    body.innerHTML = `${labelNotice}<div class="profileMain"><span class="avatar large">${escapeHtml(profile.name.slice(0, 1))}</span><div><h3>${escapeHtml(profile.name)}</h3><p>${escapeHtml(profile.email)}</p>${profile.labels.length ? `<div class="chips">${chips(profile.labels)}</div>` : ""}</div><button id="editInManagement" class="primaryButton">ユーザー管理で編集</button></div><div class="profileFacts"><span><small>エリア</small>${escapeHtml(profile.area)}</span><span><small>勤務地</small>${escapeHtml(profile.workplace)}</span><span><small>役割</small>${escapeHtml(profile.role)}</span><span><small>部門</small>${escapeHtml(profile.department)}</span><span><small>MR経験</small>${escapeHtml(profile.mrExperience)}</span></div>`;
    body.closest(".panel").classList.add("profilePanel");
    body.querySelector("#editInManagement").addEventListener("click", () => this.navigate("management", { roster: this.rosterId }));
  }

  renderSummary(summary, comparisons) {
    const difference = (value, average, unit) => value == null || average == null ? "" : `<em class="comparisonDelta ${value >= average ? "positive" : "negative"}">${value >= average ? "+" : ""}${(value - average).toFixed(1)}${unit}</em>`;
    const benchmark = (row, label) => !row
      ? `<article class="benchmark insufficient"><span>${escapeHtml(label)}</span><strong>比較情報を確認できません</strong></article>`
      : row.peerCount < 2
        ? `<article class="benchmark insufficient"><span>${label} · ${escapeHtml(row.label)}</span><strong>比較対象不足</strong><small>有効な比較対象 ${displayCount(row.peerCount)}名</small></article>`
        : `<article class="benchmark"><span>${label} · ${escapeHtml(row.label)} ${displayCount(row.peerCount)}名平均</span><div><strong>${row.averageQuestions == null ? "-" : Number(row.averageQuestions).toFixed(1)}件</strong>${difference(summary.questions, row.averageQuestions, "件")}</div><small>利用日 ${row.averageActiveDays == null ? "-" : Number(row.averageActiveDays).toFixed(1)}日 ${difference(summary.activeDays, row.averageActiveDays, "日")} / 回答成功率 ${displayMeasuredRate(row.averageCompleteDelivery)}</small></article>`;
    const body = this.body("summary");
    const completeCard = summary.completeDelivery
      ? `<article class="kpiCard measurementCard ${escapeHtml(summary.completeDelivery.measurementState)}"><span>回答成功率</span><strong class="${summary.completeDelivery.measurementState === "not_measured" ? "textValue" : ""}">${displayMeasuredRate(summary.completeDelivery)}</strong></article>`
      : '<article class="kpiCard"><span>回答成功率</span><strong class="textValue">計測情報なし</strong></article>';
    const latencyCard = summary.p95Latency
      ? `<article class="kpiCard measurementCard ${escapeHtml(summary.p95Latency.measurementState)}"><span>P95応答時間</span><strong>${displayMeasuredDuration(summary.p95Latency)}</strong></article>`
      : '<article class="kpiCard"><span>P95応答時間</span><strong class="textValue">計測情報なし</strong></article>';
    body.innerHTML = `<div class="kpiGrid personal"><article class="kpiCard"><span>最終利用（全期間）</span><strong class="smallValue">${displayDateTime(summary.lastActiveAt)}</strong></article><article class="kpiCard"><span>期間内利用日数</span><strong>${displayCount(summary.activeDays)}</strong><small>日</small></article><article class="kpiCard"><span>期間内質問数</span><strong>${displayCount(summary.questions)}</strong><small>件</small></article><article class="kpiCard"><span>1日平均質問</span><strong>${summary.questionsPerActiveDay == null ? "-" : Number(summary.questionsPerActiveDay).toFixed(1)}</strong><small>件</small></article>${completeCard}${latencyCard}</div><div class="benchmarkGrid">${benchmark(comparisons.area, "同じ地域")}${benchmark(comparisons.role, "同じ役割")}</div>`;
  }

  renderChart(canvas, create, { strictRender = false } = {}) {
    try {
      create();
    } catch (error) {
      if (strictRender) throw error;
      if (canvas?.parentElement) {
        canvas.parentElement.innerHTML = moduleMessage(
          error?.message || "グラフを表示できませんでした。",
          "error",
        );
      }
    }
  }

  renderTrend(model, { strictRender = false } = {}) {
    const body = this.body("trend");
    if (!model.rows.length) { body.innerHTML = moduleMessage("この期間の利用記録はありません。", "empty"); return; }
    body.innerHTML = `<div class="chartBox tall"><canvas id="personalTrend"></canvas></div>`;
    const canvas = body.querySelector("#personalTrend");
    this.renderChart(
      canvas,
      () => trendChart(canvas, model.rows),
      { strictRender },
    );
  }

  renderNeeds(model, { strictRender = false } = {}) {
    const body = this.body("needs");
    const dataContent = (measurement, rows, content, label) => {
      if (measurement?.measurementState === "not_measured") return moduleMessage(`この期間の${label}は確認できません。`, "not_measured");
      if (!Array.isArray(rows)) return moduleMessage(`${label}を確認できません。`, "error");
      if (!rows.length || measurement?.measurementState === "no_usage") return moduleMessage(`この期間の${label}はありません。`, "empty");
      return content;
    };
    const dimension = (measurement, rows, canvasId, label) => dataContent(measurement, rows, `<div class="chartBox compactChart"><canvas id="${canvasId}"></canvas></div>`, label);
    const products = dataContent(model.productResolution, model.products, '<div class="chartBox"><canvas id="personalProducts"></canvas></div>', "製品情報");
    const categories = dataContent(model.questionCategoryMeasurement, model.questionCategories, '<div class="chartBox"><canvas id="personalCategories"></canvas></div>', "質問テーマ");
    const taskRows = Array.isArray(model.tasks) ? model.tasks.map((row) => `<span>${escapeHtml(row.label)} <b>${displayCount(row.count)}</b></span>`).join("") : "";
    const tasks = dataContent(model.taskMeasurement, model.tasks, `<div class="taskList">${taskRows}</div>`, "質問種類");
    body.innerHTML = `<div class="needsPrimary"><article class="subPanel"><h4>よく聞く製品</h4>${products}</article><article class="subPanel"><h4>質問テーマ</h4>${categories}</article></div><article class="taskPanel"><h4>質問種類</h4>${tasks}</article><div class="needsSecondary"><article class="subPanel"><h4>モード</h4>${dimension(model.modeMeasurement, model.modes, "personalModes", "利用モード")}</article><article class="subPanel"><h4>デバイス</h4>${dimension(model.deviceMeasurement, model.devices, "personalDevices", "デバイス")}</article></div>`;
    const charts = [
      ["#personalProducts", (canvas) => barChart(canvas, model.products || [], { horizontal: true, label: "質問数", color: "#7f88ff", summary: "よく質問する製品" })],
      ["#personalCategories", (canvas) => barChart(canvas, model.questionCategories || [], { horizontal: true, label: "質問数", color: "#2fd5c4", summary: "質問テーマ別の質問数" })],
      ["#personalModes", (canvas) => doughnutChart(canvas, model.modes || [], { summary: "利用モードの構成" })],
      ["#personalDevices", (canvas) => doughnutChart(canvas, model.devices || [], { summary: "利用デバイスの構成" })],
    ];
    charts.forEach(([selector, create]) => {
      const canvas = body.querySelector(selector);
      if (canvas) this.renderChart(canvas, () => create(canvas), { strictRender });
    });
  }

  cancelConversationRequest() {
    this.conversationRequest?.abort();
    this.conversationAbortCleanup?.();
    this.conversationRequest = null;
    this.conversationAbortCleanup = null;
    this.conversationGeneration += 1;
  }

  newConversationController() {
    this.cancelConversationRequest();
    const controller = new AbortController();
    const abort = () => controller.abort(this.signal.reason);
    if (this.signal.aborted) abort(); else this.signal.addEventListener("abort", abort, { once: true });
    this.conversationAbortCleanup = () => this.signal.removeEventListener("abort", abort);
    this.conversationRequest = controller;
    return { controller, generation: this.conversationGeneration };
  }

  async loadConversations(rosterId = this.rosterId) {
    const requestRosterId = String(rosterId || "");
    if (!requestRosterId) return;
    const { controller, generation } = this.newConversationController();
    try {
      const model = conversationsModel(await getUserConversations(
        { roster_id: requestRosterId },
        { signal: controller.signal },
      ));
      if (
        !this.isCurrent()
        || generation !== this.conversationGeneration
        || requestRosterId !== this.rosterId
      ) return;
      const body = this.body("conversations");
      if (!body) return;
      body.innerHTML = '<div class="conversationJourney"><aside id="conversationList" class="conversationList" aria-label="会話一覧"></aside><div id="messageList" class="messageList" aria-live="polite"></div></div>';
      if (model.status === "identity_unmatched") {
        body.querySelector("#conversationList").innerHTML = moduleMessage("LCS利用履歴との紐付けがまだありません。");
        body.querySelector("#messageList").innerHTML = moduleMessage("会話はありません。");
        return;
      }
      this.renderConversations(model.conversations, model.issues);
    } catch (error) {
      if (isCancellation(error)) return;
      if (
        this.isCurrent()
        && generation === this.conversationGeneration
        && requestRosterId === this.rosterId
      ) this.fail("conversations", error);
    } finally {
      if (generation === this.conversationGeneration) {
        this.conversationAbortCleanup?.();
        this.conversationAbortCleanup = null;
        this.conversationRequest = null;
      }
    }
  }

  renderConversations(rows, issues = []) {
    const list = this.root.querySelector("#conversationList");
    const messages = this.root.querySelector("#messageList");
    list.innerHTML = rows.map((row) => `<button class="conversationItem" data-conversation="${escapeHtml(row.conversationId)}"><span>${escapeHtml(row.title || "無題の会話")}</span><small>${escapeHtml(row.updatedAtJst || row.updatedAt || "-")} · ${displayCount(row.messageCount)}件</small></button>`).join("") || moduleMessage("会話はありません。");
    messages.innerHTML = `${moduleMessage(rows.length ? "左側から会話を選択してください。" : "メッセージはありません。")}`;
    list.querySelectorAll(".conversationItem").forEach((button) => button.addEventListener("click", () => {
      list.querySelectorAll(".conversationItem").forEach((item) => item.classList.remove("isSelected"));
      button.classList.add("isSelected");
      this.selectedConversation = button.dataset.conversation;
      this.messageCursor = "";
      this.loadMessages(false);
    }));
    const latest = list.querySelector(".conversationItem");
    if (latest) latest.click();
  }

  newMessageController() {
    this.messageRequest?.abort();
    this.messageAbortCleanup?.();
    const controller = new AbortController();
    const abort = () => controller.abort(this.signal.reason);
    if (this.signal.aborted) abort(); else this.signal.addEventListener("abort", abort, { once: true });
    this.messageAbortCleanup = () => this.signal.removeEventListener("abort", abort);
    this.messageRequest = controller;
    return controller;
  }

  async loadMessages(append) {
    if (append && this.messageLoading) return;
    const target = this.root.querySelector("#messageList");
    if (!target || !this.selectedConversation) return;
    const conversationId = this.selectedConversation;
    const cursor = append ? this.messageCursor : "";
    const generation = ++this.messageGeneration;
    const controller = this.newMessageController();
    this.messageLoading = true;
    if (!append) target.innerHTML = moduleMessage("読み込み中…");
    else target.querySelector("#loadMoreMessages")?.setAttribute("disabled", "");
    try {
      const model = messageModel(await getTraceMessages({ roster_id: this.rosterId, conversation_id: conversationId, cursor, limit: 100 }, { signal: controller.signal }));
      if (!this.isCurrent() || generation !== this.messageGeneration || conversationId !== this.selectedConversation) return;
      const html = model.messages.map((row) => `<article class="messageBubble ${escapeHtml(row.role)}"><header><strong>${escapeHtml(row.roleLabel || row.role)}</strong><time>${escapeHtml(row.timestampJst || "-")}</time></header><p>${escapeHtml(row.content || "-")}</p></article>`).join("") || moduleMessage("メッセージはありません。");
      if (append) target.querySelector("#loadMoreMessages")?.remove();
      if (append) target.insertAdjacentHTML("beforeend", html); else target.innerHTML = html;
      this.messageCursor = model.nextCursor;
      if (this.messageCursor) {
        target.insertAdjacentHTML("beforeend", '<button id="loadMoreMessages" class="ghostButton">さらに読み込む</button>');
        target.querySelector("#loadMoreMessages").addEventListener("click", () => this.loadMessages(true));
      }
    } catch (error) {
      if (!isCancellation(error) && this.isCurrent() && generation === this.messageGeneration) {
        if (append) {
          target.querySelector("#loadMoreMessages")?.remove();
          target.insertAdjacentHTML("beforeend", moduleMessage(error.message, "error"));
        } else target.innerHTML = moduleMessage(error.message, "error");
      }
    } finally {
      if (generation === this.messageGeneration) {
        this.messageLoading = false;
        this.messageAbortCleanup?.();
        this.messageAbortCleanup = null;
      }
    }
  }
}
