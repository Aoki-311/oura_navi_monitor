import { getOverview, getRegions, getUsers, isCancellation } from "../api/client.js";
import {
  activityModel, categoryModel, environmentModel, kpisModel, overviewEnvelope,
  productsModel, regionsModel, usageTrendModel,
} from "../adapters/overviewAdapter.js";
import { usersModel } from "../adapters/usersAdapter.js";
import { barChart, doughnutChart, stackedChart, usageTrendChart } from "../components/charts.js";
import { chips, escapeHtml, moduleMessage, setBusy } from "../components/dom.js";
import { renderJapanMap } from "../components/japanMap.js";
import { renderProductMatrix } from "../components/productMatrix.js";
import { displayCount, displayDateTime, displayDuration, displayRate, measurementCoverage } from "../viewModels/formatters.js";

const OVERVIEW_MODULES = ["kpis", "environment", "usage", "categories", "activity", "products"];

export class OverviewPage {
  constructor(root, { navigate, toast, getPreset, state, signal, isCurrent, setArea }) {
    this.root = root;
    this.navigate = navigate;
    this.toast = toast;
    this.getPreset = getPreset;
    this.areaKey = state.area;
    this.signal = signal;
    this.isCurrent = isCurrent;
    this.setArea = setArea;
  }

  async load() {
    setBusy(this.root, true);
    this.root.innerHTML = this.shell();
    await Promise.allSettled([this.loadOverview(), this.loadRegions(), this.loadUsers()]);
    if (this.isCurrent()) setBusy(this.root, false);
  }

  shell() {
    return `
      <div class="pageHeading"><div><p class="eyebrow"><span data-global-count>全体対象</span> / <span data-user-count>ユーザー・地域対象</span></p><h2>全体サマリー</h2><p>利用の広がり、回答の完全交付、地域差と製品ニーズを一画面で確認します。</p></div><div id="areaChip"></div></div>
      <section class="panel" data-module="kpis"><div class="panelHead"><h3><span>01</span>主要KPI</h3><small>対象: MR・ヘルスケア本社</small></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
      <section class="panel" data-module="environment"><div class="panelHead"><h3><span>02</span>利用環境・モード</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
      <section class="twoGrid"><article class="panel" data-module="usage"><div class="panelHead"><h3><span>03</span>利用推移</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div></article><article class="panel" data-module="categories"><div class="panelHead"><h3>質問タイプ</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div></article></section>
      <section class="panel" data-module="activity"><div class="panelHead"><h3><span>04</span>活性度分布</h3><small>活性度は選択期間に関係なく直近14日で判定</small></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
      <section class="panel" data-module="users"><div class="panelHead"><h3><span>05</span>ユーザー一覧</h3><small>対象: 管理者を除くユーザー</small></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>
      <section class="twoGrid mapGrid"><article class="panel" data-module="map"><div class="panelHead"><h3><span>06</span>日本利用マップ</h3><small>色の濃さ: 期間利用者数</small></div><div data-module-body>${moduleMessage("読み込み中…")}</div></article><article class="panel" data-module="ranking"><div class="panelHead"><h3>地域ランキング</h3><small>期間利用者数、次に質問数の順</small></div><div data-module-body>${moduleMessage("読み込み中…")}</div></article></section>
      <section class="panel" data-module="products"><div class="panelHead"><h3><span>07</span>製品ニーズ</h3></div><div data-module-body>${moduleMessage("読み込み中…")}</div></section>`;
  }

  body(name) {
    return this.root.querySelector(`[data-module="${name}"] [data-module-body]`);
  }

  fail(name, error) {
    if (!this.isCurrent() || isCancellation(error)) return;
    const target = this.body(name);
    if (target) target.innerHTML = moduleMessage(error?.message || "データを取得できませんでした。", "error");
  }

  renderPart(name, render) {
    try { render(); } catch (error) { this.fail(name, error); }
  }

  async loadOverview() {
    try {
      const raw = await getOverview({ preset: this.getPreset(), area_key: this.areaKey }, { signal: this.signal });
      if (!this.isCurrent()) return;
      const envelope = overviewEnvelope(raw);
      this.root.querySelector("[data-global-count]").textContent = `全体指標 ${envelope.scopeUserCount}名`;
      this.renderPart("kpis", () => this.renderKpis(kpisModel(raw)));
      this.renderPart("environment", () => this.renderEnvironment(environmentModel(raw)));
      this.renderPart("usage", () => this.renderUsage(usageTrendModel(raw)));
      this.renderPart("categories", () => this.renderCategories(categoryModel(raw)));
      this.renderPart("activity", () => this.renderActivity(activityModel(raw)));
      this.renderPart("products", () => this.renderProducts(productsModel(raw)));
    } catch (error) {
      if (isCancellation(error)) throw error;
      OVERVIEW_MODULES.forEach((name) => this.fail(name, error));
    }
  }

  renderKpis(model) {
    const body = this.body("kpis");
    const kpis = [
      ["期間利用者数", displayCount(model.activeUsers), "人"],
      ["利用率", displayRate(model.adoptionRate), ""],
      ["再訪率", displayRate(model.returnRate), ""],
      ["1人あたり質問", model.questionsPerActiveUser == null ? "-" : Number(model.questionsPerActiveUser).toFixed(1), "件"],
      ["回答成功率（完全交付）", displayRate(model.completeDelivery.value), ""],
      ["P95応答時間", displayDuration(model.p95Latency.valueMs), ""],
    ];
    const notes = [];
    if (model.returnRate == null && this.getPreset() === "today") notes.push("今日の再訪率は1日内で定義できないため「-」です。");
    if (model.completeDelivery.measuredCount !== model.completeDelivery.totalCount) notes.push(`回答成功率: ${measurementCoverage(model.completeDelivery)}`);
    if (model.p95Latency.measuredCount !== model.p95Latency.totalCount) notes.push(`P95応答時間: ${measurementCoverage(model.p95Latency)}`);
    body.innerHTML = `<div id="kpis" class="kpiGrid">${kpis.map(([label, value, unit]) => `<article class="kpiCard"><span>${label}</span><strong>${value}</strong><small>${unit}</small></article>`).join("")}</div>${notes.length ? `<p class="measurementNote">${notes.map(escapeHtml).join(" / ")}</p>` : ""}`;
  }

  renderEnvironment(model) {
    const body = this.body("environment");
    body.innerHTML = `<div class="threeGrid"><article class="subPanel"><h4>時間帯別質問</h4><div class="chartBox"><canvas id="hourChart"></canvas></div></article><article class="subPanel"><h4>デバイス</h4><div class="chartBox"><canvas id="deviceChart"></canvas></div></article><article class="subPanel"><h4>利用モード</h4><div class="chartBox"><canvas id="modeChart"></canvas></div></article></div>`;
    barChart(body.querySelector("#hourChart"), model.hourlyQuestions, { label: "質問数", summary: "時間帯別の質問数" });
    doughnutChart(body.querySelector("#deviceChart"), model.deviceDistribution, { summary: "デバイス別の利用構成" });
    doughnutChart(body.querySelector("#modeChart"), model.modeDistribution, { summary: "利用モード別の構成" });
  }

  renderUsage(rows) {
    const body = this.body("usage");
    body.innerHTML = '<div class="chartBox tall"><canvas id="usageChart"></canvas></div>';
    usageTrendChart(body.querySelector("#usageChart"), rows);
  }

  renderCategories(rows) {
    const body = this.body("categories");
    const unclassified = rows.find((row) => row.key === "unclassified")?.count || 0;
    body.innerHTML = `<div class="chartBox tall"><canvas id="categoryChart"></canvas></div>${unclassified ? `<p class="measurementNote">分類できなかった質問 ${displayCount(unclassified)}件を「判定不能」として明示しています。</p>` : ""}`;
    barChart(body.querySelector("#categoryChart"), rows, { horizontal: true, label: "質問数", color: "#27d9d2", summary: "質問タイプ別の質問数" });
  }

  renderActivity(model) {
    const body = this.body("activity");
    body.innerHTML = '<div class="activityGrid"><div class="chartBox donut"><canvas id="activityChart"></canvas></div><div><h4>地域別構成</h4><div class="chartBox"><canvas id="areaActivityChart"></canvas></div></div><div><h4>役割別構成</h4><div class="chartBox"><canvas id="roleActivityChart"></canvas></div></div></div>';
    doughnutChart(body.querySelector("#activityChart"), model.distribution, { summary: "全体の活性度構成" });
    stackedChart(body.querySelector("#areaActivityChart"), model.byArea, { summary: "地域別の活性度構成" });
    stackedChart(body.querySelector("#roleActivityChart"), model.byRole, { summary: "役割別の活性度構成" });
  }

  renderProducts(model) {
    const body = this.body("products");
    const unresolved = Number.isInteger(model.resolution.unresolvedQuestions) ? model.resolution.unresolvedQuestions : 0;
    body.innerHTML = `<div class="twoGrid productGrid"><article class="subPanel"><h4>質問対象（製品）Top 10</h4><div class="chartBox tall"><canvas id="productChart"></canvas></div></article><article class="subPanel matrixPanel"><h4>製品 × 質問タイプ</h4><div id="productMatrix" class="productMatrix"></div></article></div>${unresolved ? `<p class="measurementNote">正式な製品名を確認できなかった質問 ${displayCount(unresolved)}件は、ランキングとマトリクスに含めていません。</p>` : ""}`;
    barChart(body.querySelector("#productChart"), model.topProducts, { horizontal: true, label: "質問数", color: "#8f72ff", summary: "質問対象製品の上位10件" });
    renderProductMatrix(body.querySelector("#productMatrix"), model.matrix);
  }

  async loadRegions() {
    try {
      const model = regionsModel(await getRegions({ preset: this.getPreset() }, { signal: this.signal }));
      if (!this.isCurrent()) return;
      this.root.querySelector("[data-user-count]").textContent = `ユーザー・地域分析 ${model.scopeUserCount}名`;
      this.renderRanking(model.regions, model.issues);
      try { await this.renderMap(model.regions); } catch (error) { this.fail("map", error); }
      this.renderAreaChip(model.regions);
    } catch (error) {
      if (isCancellation(error)) throw error;
      this.fail("map", error);
      this.fail("ranking", error);
    }
  }

  renderRanking(rows, issues = []) {
    const body = this.body("ranking");
    body.innerHTML = `<div id="regionRanking" class="rankingList">${rows.map((row, index) => `<button class="rankingRow ${row.areaKey === this.areaKey ? "isSelected" : ""}" data-area="${escapeHtml(row.areaKey)}"><b>${index + 1}</b><span><strong>${escapeHtml(row.area)}</strong><small>${displayCount(row.activeUsers)}人 / ${displayCount(row.questions)}件</small></span><em>${displayRate(row.adoptionRate)}</em></button>`).join("") || moduleMessage("地域データはありません。")}</div>${issues.length ? `<p class="measurementNote">${displayCount(issues.length)}件の不正な地域行を表示対象から外しました。</p>` : ""}`;
    body.querySelectorAll(".rankingRow").forEach((button) => button.addEventListener("click", () => this.setArea(button.dataset.area === this.areaKey ? "" : button.dataset.area)));
  }

  async renderMap(rows) {
    const body = this.body("map");
    body.innerHTML = '<div id="japanMap" class="mapCanvas"></div><div class="mapLegend" aria-label="地図の色の説明"><span>少ない</span><i></i><span>多い</span></div>';
    await renderJapanMap(body.querySelector("#japanMap"), rows, { selectedAreaKey: this.areaKey, onSelect: (key) => this.setArea(key === this.areaKey ? "" : key), signal: this.signal });
  }

  renderAreaChip(rows) {
    const selected = rows.find((row) => row.areaKey === this.areaKey);
    this.root.querySelector("#areaChip").innerHTML = selected ? `<button class="filterChip" id="clearArea" aria-label="${escapeHtml(selected.area)}の絞り込みを解除">${escapeHtml(selected.area)} <span aria-hidden="true">×</span></button>` : "";
    this.root.querySelector("#clearArea")?.addEventListener("click", () => this.setArea(""));
  }

  async loadUsers() {
    try {
      const model = usersModel(await getUsers({ preset: this.getPreset(), area_key: this.areaKey }, { signal: this.signal }));
      if (!this.isCurrent()) return;
      this.root.querySelector("[data-user-count]").textContent = `ユーザー・地域分析 ${model.scopeUserCount}名`;
      const body = this.body("users");
      body.innerHTML = `<div class="tableScroll" tabindex="0" aria-label="ユーザー一覧。横方向にスクロールできます。"><table id="overviewUsers"><thead><tr><th>社員名</th><th>メール</th><th>エリア</th><th>最終利用</th><th>直近7日利用日数</th><th>直近7日メッセージ数</th><th>回答成功率</th><th>活性度</th><th></th></tr></thead><tbody>${model.users.map((row) => `<tr><td><strong>${escapeHtml(row.name)}</strong><div class="chips">${chips(row.labels)}</div></td><td>${escapeHtml(row.email)}</td><td>${escapeHtml(row.area)}</td><td>${displayDateTime(row.lastActiveAt)}</td><td>${displayCount(row.activeDays7)}</td><td>${displayCount(row.userMessageCount7)}</td><td title="${escapeHtml(measurementCoverage(row.completeDelivery))}">${displayRate(row.completeDelivery.value)}</td><td><span class="activityBadge ${escapeHtml(row.activity || "unknown")}">${escapeHtml(row.activityLabel)}</span></td><td><button class="linkButton" data-roster="${escapeHtml(row.rosterId)}">詳細</button></td></tr>`).join("") || `<tr><td colspan="9">${moduleMessage("対象ユーザーはいません。")}</td></tr>`}</tbody></table></div>${model.issues.length ? `<p class="measurementNote">${displayCount(model.issues.length)}件の未計測項目は「-」または「未測定」として表示しています。</p>` : ""}`;
      body.querySelectorAll("[data-roster]").forEach((button) => button.addEventListener("click", () => this.navigate("user", { roster: button.dataset.roster, area: "" })));
    } catch (error) {
      if (isCancellation(error)) throw error;
      this.fail("users", error);
    }
  }
}
