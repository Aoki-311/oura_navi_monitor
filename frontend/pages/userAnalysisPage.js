import { getTraceMessages, getUserConversations, getUserDetail, getUsers, isCancellation } from "../api/client.js";
import {
  conversationsModel, messageModel, userComparisonsModel, userDetailEnvelope,
  userNeedsModel, userProfileModel, userSummaryModel, userTrendModel, usersModel,
} from "../adapters/usersAdapter.js";
import { barChart, doughnutChart, trendChart } from "../components/charts.js";
import { chips, escapeHtml, moduleMessage, setBusy } from "../components/dom.js";
import { displayCount, displayDateTime, displayRate, measurementCoverage } from "../viewModels/formatters.js";

const DETAIL_MODULES = ["profile", "summary", "trend", "needs"];

export class UserAnalysisPage {
  constructor(root, { rosterId = "", navigate, toast, getPreset, signal, isCurrent }) {
    this.root = root;
    this.rosterId = rosterId;
    this.navigate = navigate;
    this.toast = toast;
    this.getPreset = getPreset;
    this.signal = signal;
    this.isCurrent = isCurrent;
    this.selectedConversation = "";
    this.messageCursor = "";
    this.messageRequest = null;
    this.messageAbortCleanup = null;
    this.messageGeneration = 0;
    this.messageLoading = false;
  }

  async load() {
    setBusy(this.root, true);
    if (!this.rosterId) {
      await this.renderChooser();
      if (this.isCurrent()) setBusy(this.root, false);
      return;
    }
    this.root.innerHTML = this.shell();
    this.root.querySelector("#changeUser").addEventListener("click", () => this.navigate("user", { roster: "" }));
    await Promise.allSettled([this.loadDetail(), this.loadConversations()]);
    if (this.isCurrent()) setBusy(this.root, false);
  }

  shell() {
    return `<div class="pageHeading"><div><p class="eyebrow">個人の利用状況とニーズ</p><h2>ユーザー分析</h2></div><button class="ghostButton" id="changeUser">別のユーザーを選択</button></div>
      <section class="panel" data-module="profile"><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
      <section class="panel" data-module="summary"><div class="panelHead"><h3><span>01</span>個人利用サマリー</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
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

  async renderChooser() {
    this.root.innerHTML = `<div class="pageHeading"><div><p class="eyebrow">管理者を除くユーザー</p><h2>ユーザー分析</h2><p>分析するユーザーを選択してください。</p></div></div><section class="panel chooserPanel"><label>ユーザー検索<input id="userSearch" type="search" placeholder="氏名またはメール"></label><div id="userChoices" class="userChoices">${moduleMessage("読み込み中…")}</div></section>`;
    try {
      const model = usersModel(await getUsers({ preset: this.getPreset() }, { signal: this.signal }));
      if (!this.isCurrent()) return;
      const render = (keyword = "") => {
        const query = keyword.trim().toLowerCase();
        const rows = model.users.filter((row) => !query || `${row.name} ${row.email}`.toLowerCase().includes(query));
        this.root.querySelector("#userChoices").innerHTML = rows.map((row) => `<button class="userChoice" data-roster="${escapeHtml(row.rosterId)}"><span class="avatar">${escapeHtml(row.name.slice(0, 1))}</span><span><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)} · ${escapeHtml(row.area)}</small></span><em>${escapeHtml(row.activityLabel)}</em></button>`).join("") || moduleMessage("該当ユーザーはいません。");
        this.root.querySelectorAll(".userChoice").forEach((button) => button.addEventListener("click", () => this.navigate("user", { roster: button.dataset.roster })));
      };
      render();
      this.root.querySelector("#userSearch").addEventListener("input", (event) => render(event.target.value));
    } catch (error) {
      if (!isCancellation(error)) this.root.querySelector("#userChoices").innerHTML = moduleMessage(error.message, "error");
    }
  }

  async loadDetail() {
    try {
      const raw = await getUserDetail(this.rosterId, { preset: this.getPreset() }, { signal: this.signal });
      if (!this.isCurrent()) return;
      userDetailEnvelope(raw);
      try { this.renderProfile(userProfileModel(raw)); } catch (error) { this.fail("profile", error); }
      try { this.renderSummary(userSummaryModel(raw), userComparisonsModel(raw)); } catch (error) { this.fail("summary", error); }
      try { this.renderTrend(userTrendModel(raw)); } catch (error) { this.fail("trend", error); }
      try { this.renderNeeds(userNeedsModel(raw)); } catch (error) { this.fail("needs", error); }
    } catch (error) {
      if (isCancellation(error)) throw error;
      DETAIL_MODULES.forEach((name) => this.fail(name, error));
    }
  }

  renderProfile(profile) {
    const body = this.body("profile");
    body.innerHTML = `<div class="profileMain"><span class="avatar large">${escapeHtml(profile.name.slice(0, 1))}</span><div><h3>${escapeHtml(profile.name)}</h3><p>${escapeHtml(profile.email)}</p><div class="chips">${chips(profile.labels)}</div></div><button id="editInManagement" class="primaryButton">ユーザー管理で編集</button></div><div class="profileFacts"><span><small>エリア</small>${escapeHtml(profile.area)}</span><span><small>勤務地</small>${escapeHtml(profile.workplace)}</span><span><small>役割</small>${escapeHtml(profile.role)}</span><span><small>部門</small>${escapeHtml(profile.department)}</span><span><small>MR経験</small>${escapeHtml(profile.mrExperience)}</span></div>`;
    body.closest(".panel").classList.add("profilePanel");
    body.querySelector("#editInManagement").addEventListener("click", () => this.navigate("management", { roster: this.rosterId }));
  }

  renderSummary(summary, comparisons) {
    const benchmark = (row, label) => `<article class="benchmark"><span>${label}平均 · ${escapeHtml(row.label)}</span><strong>${row.averageQuestions == null ? "-" : Number(row.averageQuestions).toFixed(1)}件</strong><small>利用日 ${row.averageActiveDays == null ? "-" : Number(row.averageActiveDays).toFixed(1)}日 / 完全交付 ${displayRate(row.averageCompleteDelivery.value)}（${escapeHtml(measurementCoverage(row.averageCompleteDelivery))}）</small></article>`;
    const body = this.body("summary");
    body.innerHTML = `<div class="kpiGrid personal"><article class="kpiCard"><span>最終利用</span><strong class="smallValue">${displayDateTime(summary.lastActiveAt)}</strong></article><article class="kpiCard"><span>利用日数</span><strong>${displayCount(summary.activeDays)}</strong><small>日</small></article><article class="kpiCard"><span>質問数</span><strong>${displayCount(summary.questions)}</strong><small>件</small></article><article class="kpiCard"><span>1日平均質問</span><strong>${summary.questionsPerActiveDay == null ? "-" : Number(summary.questionsPerActiveDay).toFixed(1)}</strong><small>件</small></article><article class="kpiCard"><span>回答成功率</span><strong>${displayRate(summary.completeDelivery.value)}</strong><small title="${escapeHtml(measurementCoverage(summary.completeDelivery))}">${summary.completeDelivery.measuredCount}/${summary.completeDelivery.totalCount}件計測</small></article></div><div class="benchmarkGrid">${benchmark(comparisons.area, "同じ地域")}${benchmark(comparisons.role, "同じ役割")}</div>`;
  }

  renderTrend(rows) {
    const body = this.body("trend");
    body.innerHTML = '<div class="chartBox tall"><canvas id="personalTrend"></canvas></div>';
    trendChart(body.querySelector("#personalTrend"), rows);
  }

  renderNeeds(model) {
    const unresolved = Number.isInteger(model.productResolution.unresolvedQuestions) ? model.productResolution.unresolvedQuestions : 0;
    const body = this.body("needs");
    body.innerHTML = `<div class="fourGrid"><article class="subPanel"><h4>よく聞く製品</h4><div class="chartBox"><canvas id="personalProducts"></canvas></div></article><article class="subPanel"><h4>質問タイプ</h4><div class="chartBox"><canvas id="personalCategories"></canvas></div></article><article class="subPanel"><h4>モード</h4><div class="chartBox"><canvas id="personalModes"></canvas></div></article><article class="subPanel"><h4>デバイス</h4><div class="chartBox"><canvas id="personalDevices"></canvas></div></article></div><div class="taskList"><strong>よくあるタスク</strong>${model.tasks.length ? model.tasks.map((row) => `<span>${escapeHtml(row.label)} <b>${displayCount(row.count)}</b></span>`).join("") : '<span class="muted">データなし</span>'}</div>${unresolved ? `<p class="measurementNote">正式な製品名を確認できなかった質問 ${displayCount(unresolved)}件は、製品ランキングに含めていません。</p>` : ""}`;
    barChart(body.querySelector("#personalProducts"), model.products, { horizontal: true, label: "質問数", color: "#8f72ff", summary: "よく質問する製品" });
    barChart(body.querySelector("#personalCategories"), model.questionCategories, { horizontal: true, label: "質問数", color: "#27d9d2", summary: "質問タイプ別の質問数" });
    doughnutChart(body.querySelector("#personalModes"), model.modes, { summary: "利用モードの構成" });
    doughnutChart(body.querySelector("#personalDevices"), model.devices, { summary: "利用デバイスの構成" });
  }

  async loadConversations() {
    try {
      const model = conversationsModel(await getUserConversations({ roster_id: this.rosterId }, { signal: this.signal }));
      if (!this.isCurrent()) return;
      const body = this.body("conversations");
      body.innerHTML = '<div class="conversationJourney"><aside id="conversationList" class="conversationList" aria-label="会話一覧"></aside><div id="messageList" class="messageList" aria-live="polite"></div></div>';
      if (model.status === "identity_unmatched") {
        body.querySelector("#conversationList").innerHTML = moduleMessage("LCS利用履歴との紐付けがまだありません。");
        body.querySelector("#messageList").innerHTML = moduleMessage("会話はありません。");
        return;
      }
      this.renderConversations(model.conversations, model.issues);
    } catch (error) {
      if (isCancellation(error)) throw error;
      this.fail("conversations", error);
    }
  }

  renderConversations(rows, issues = []) {
    const list = this.root.querySelector("#conversationList");
    const messages = this.root.querySelector("#messageList");
    list.innerHTML = rows.map((row) => `<button class="conversationItem" data-conversation="${escapeHtml(row.conversationId)}"><span>${escapeHtml(row.title || "無題の会話")}</span><small>${escapeHtml(row.updatedAtJst || row.updatedAt || "-")} · ${displayCount(row.messageCount)}件</small></button>`).join("") || moduleMessage("会話はありません。");
    messages.innerHTML = `${moduleMessage(rows.length ? "左側から会話を選択してください。" : "メッセージはありません。")}${issues.length ? `<p class="measurementNote">${displayCount(issues.length)}件の不正な会話行を表示対象から外しました。</p>` : ""}`;
    list.querySelectorAll(".conversationItem").forEach((button) => button.addEventListener("click", () => {
      list.querySelectorAll(".conversationItem").forEach((item) => item.classList.remove("isSelected"));
      button.classList.add("isSelected");
      this.selectedConversation = button.dataset.conversation;
      this.messageCursor = "";
      this.loadMessages(false);
    }));
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
      if (model.issues.length) target.insertAdjacentHTML("beforeend", `<p class="measurementNote">${displayCount(model.issues.length)}件の不正なメッセージ行を表示対象から外しました。</p>`);
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
