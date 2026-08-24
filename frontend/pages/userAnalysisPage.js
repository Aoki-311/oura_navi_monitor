import { getTraceMessages, getUserDetail, getUsers } from "../api/client.js";
import { messageModel, userDetailModel, usersModel } from "../adapters/usersAdapter.js";
import { barChart, doughnutChart, trendChart } from "../components/charts.js";
import { chips, escapeHtml, moduleMessage, setBusy } from "../components/dom.js";
import { displayCount, displayDateTime, displayRate } from "../viewModels/formatters.js";

export class UserAnalysisPage {
  constructor(root, { rosterId = "", navigate, toast, getPreset }) {
    this.root = root;
    this.rosterId = rosterId;
    this.navigate = navigate;
    this.toast = toast;
    this.getPreset = getPreset;
    this.selectedConversation = "";
    this.messageCursor = "";
  }

  async load() {
    setBusy(this.root, true);
    if (!this.rosterId) {
      await this.renderChooser();
      setBusy(this.root, false);
      return;
    }
    this.root.innerHTML = `<div class="pageHeading"><div><p class="eyebrow">個人の利用状況とニーズ</p><h2>ユーザー分析</h2></div><button class="ghostButton" id="changeUser">別のユーザーを選択</button></div><div id="detailContent">${moduleMessage("読み込み中…")}</div>`;
    this.root.querySelector("#changeUser").addEventListener("click", () => this.navigate("user", {}));
    try {
      const payload = userDetailModel(await getUserDetail(this.rosterId, { preset: this.getPreset() }));
      this.render(payload);
    } catch (error) {
      this.root.querySelector("#detailContent").innerHTML = moduleMessage(error.message);
      this.toast(error.message, "error");
    }
    setBusy(this.root, false);
  }

  async renderChooser() {
    this.root.innerHTML = `<div class="pageHeading"><div><p class="eyebrow">管理者を除く80名</p><h2>ユーザー分析</h2><p>分析するユーザーを選択してください。</p></div></div><section class="panel chooserPanel"><label>ユーザー検索<input id="userSearch" type="search" placeholder="氏名またはメール"></label><div id="userChoices" class="userChoices">${moduleMessage("読み込み中…")}</div></section>`;
    try {
      const model = usersModel(await getUsers({ preset: this.getPreset() }));
      const render = (keyword = "") => {
        const query = keyword.trim().toLowerCase();
        const rows = model.users.filter((row) => !query || `${row.name} ${row.email}`.toLowerCase().includes(query));
        this.root.querySelector("#userChoices").innerHTML = rows.map((row) => `<button class="userChoice" data-roster="${escapeHtml(row.rosterId)}"><span class="avatar">${escapeHtml(row.name.slice(0, 1))}</span><span><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)} · ${escapeHtml(row.area)}</small></span><em>${escapeHtml(row.activityLabel)}</em></button>`).join("") || moduleMessage("該当ユーザーはいません");
        this.root.querySelectorAll(".userChoice").forEach((button) => button.addEventListener("click", () => this.navigate("user", { roster: button.dataset.roster })));
      };
      render();
      this.root.querySelector("#userSearch").addEventListener("input", (event) => render(event.target.value));
    } catch (error) {
      this.root.querySelector("#userChoices").innerHTML = moduleMessage(error.message);
    }
  }

  render(model) {
    const profile = model.profile;
    const summary = model.summary;
    const benchmark = (kind, label) => {
      const row = model.comparisons[kind];
      return `<article class="benchmark"><span>${label}平均 · ${escapeHtml(row.label)}</span><strong>${row.averageQuestions == null ? "-" : Number(row.averageQuestions).toFixed(1)}件</strong><small>利用日 ${row.averageActiveDays == null ? "-" : Number(row.averageActiveDays).toFixed(1)}日 / 完全交付 ${displayRate(row.averageCompleteDeliveryRate)}</small></article>`;
    };
    this.root.querySelector("#detailContent").innerHTML = `
      <section class="panel profilePanel"><div class="profileMain"><span class="avatar large">${escapeHtml(profile.name.slice(0, 1))}</span><div><h3>${escapeHtml(profile.name)}</h3><p>${escapeHtml(profile.email)}</p><div class="chips">${chips(profile.labels)}</div></div><button id="editInManagement" class="primaryButton">ユーザー管理で編集</button></div><div class="profileFacts"><span><small>エリア</small>${escapeHtml(profile.area)}</span><span><small>勤務地</small>${escapeHtml(profile.workplace)}</span><span><small>部門</small>${escapeHtml(profile.department)}</span><span><small>MR経験</small>${escapeHtml(profile.mrExperience)}</span></div></section>
      <section class="panel"><div class="panelHead"><h3><span>01</span>個人利用サマリー</h3></div><div class="kpiGrid personal"><article class="kpiCard"><span>最終利用</span><strong class="smallValue">${displayDateTime(summary.lastActiveAt)}</strong></article><article class="kpiCard"><span>利用日数</span><strong>${displayCount(summary.activeDays)}</strong><small>日</small></article><article class="kpiCard"><span>質問数</span><strong>${displayCount(summary.questions)}</strong><small>件</small></article><article class="kpiCard"><span>1日平均質問</span><strong>${summary.questionsPerActiveDay == null ? "-" : Number(summary.questionsPerActiveDay).toFixed(1)}</strong><small>件</small></article><article class="kpiCard"><span>回答成功率</span><strong>${displayRate(summary.completeDeliveryRate)}</strong></article></div><div class="benchmarkGrid">${benchmark("area", "同じ地域")}${benchmark("role", "同じ役割")}</div></section>
      <section class="panel"><div class="panelHead"><h3><span>02</span>個人利用推移</h3></div><div class="chartBox tall"><canvas id="personalTrend"></canvas></div></section>
      <section class="panel"><div class="panelHead"><h3><span>03</span>ユーザーニーズ傾向</h3></div><div class="fourGrid"><article class="subPanel"><h4>よく聞く製品</h4><div class="chartBox"><canvas id="personalProducts"></canvas></div></article><article class="subPanel"><h4>質問タイプ</h4><div class="chartBox"><canvas id="personalCategories"></canvas></div></article><article class="subPanel"><h4>モード</h4><div class="chartBox"><canvas id="personalModes"></canvas></div></article><article class="subPanel"><h4>デバイス</h4><div class="chartBox"><canvas id="personalDevices"></canvas></div></article></div><div class="taskList"><strong>よくあるタスク</strong>${model.tasks.length ? model.tasks.map((row) => `<span>${escapeHtml(row.label)} <b>${displayCount(row.count)}</b></span>`).join("") : '<span class="muted">-</span>'}</div>${Number(model.productResolution.unresolvedQuestions || 0) ? `<p class="measurementNote">正式な製品名を確認できなかった質問 ${displayCount(model.productResolution.unresolvedQuestions)}件は、製品ランキングに含めていません。</p>` : ""}</section>
      <section class="panel"><div class="panelHead"><h3><span>04</span>会話ジャーニー</h3></div><div class="conversationJourney"><aside id="conversationList" class="conversationList"></aside><div id="messageList" class="messageList">${moduleMessage("左側から会話を選択してください")}</div></div></section>`;
    this.root.querySelector("#editInManagement").addEventListener("click", () => this.navigate("management", { roster: this.rosterId }));
    trendChart(this.root.querySelector("#personalTrend"), model.trend);
    barChart(this.root.querySelector("#personalProducts"), model.products, { horizontal: true, label: "質問数", color: "#8f72ff" });
    barChart(this.root.querySelector("#personalCategories"), model.questionCategories, { horizontal: true, label: "質問数", color: "#27d9d2" });
    doughnutChart(this.root.querySelector("#personalModes"), model.modes);
    doughnutChart(this.root.querySelector("#personalDevices"), model.devices);
    this.renderConversations(model.conversations);
  }

  renderConversations(rows) {
    const list = this.root.querySelector("#conversationList");
    list.innerHTML = rows.map((row) => `<button class="conversationItem" data-conversation="${escapeHtml(row.conversationId)}"><span>${escapeHtml(row.title || "無題の会話")}</span><small>${escapeHtml(row.updatedAtJst || row.updatedAt || "-")} · ${displayCount(row.messageCount)}件</small></button>`).join("") || moduleMessage("会話はありません");
    list.querySelectorAll(".conversationItem").forEach((button) => button.addEventListener("click", async () => {
      list.querySelectorAll(".conversationItem").forEach((item) => item.classList.remove("isSelected"));
      button.classList.add("isSelected");
      this.selectedConversation = button.dataset.conversation;
      this.messageCursor = "";
      await this.loadMessages(false);
    }));
  }

  async loadMessages(append) {
    const target = this.root.querySelector("#messageList");
    if (!append) target.innerHTML = moduleMessage("読み込み中…");
    try {
      const model = messageModel(await getTraceMessages({ roster_id: this.rosterId, conversation_id: this.selectedConversation, cursor: append ? this.messageCursor : "", limit: 100 }));
      const html = model.messages.map((row) => `<article class="messageBubble ${escapeHtml(row.role)}"><header><strong>${escapeHtml(row.roleLabel || row.role)}</strong><time>${escapeHtml(row.timestampJst || "-")}</time></header><p>${escapeHtml(row.content || "-")}</p></article>`).join("") || moduleMessage("メッセージはありません");
      if (append) target.querySelector("#loadMoreMessages")?.remove();
      target.innerHTML = append ? target.innerHTML + html : html;
      this.messageCursor = model.nextCursor;
      if (this.messageCursor) {
        target.insertAdjacentHTML("beforeend", '<button id="loadMoreMessages" class="ghostButton">さらに読み込む</button>');
        target.querySelector("#loadMoreMessages").addEventListener("click", () => this.loadMessages(true));
      }
    } catch (error) {
      target.innerHTML = moduleMessage(error.message);
    }
  }
}
