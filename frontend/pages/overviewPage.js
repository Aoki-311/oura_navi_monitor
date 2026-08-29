import { getOverview, getRegions, getUsers, isCancellation } from "../api/client.js";
import {
  activityModel, environmentModel, kpisModel, overviewEnvelope,
  productsModel, regionsModel, taskModel, usageTrendModel,
} from "../adapters/overviewAdapter.js";
import { usersModel } from "../adapters/usersAdapter.js";
import { barChart, doughnutChart, stackedChart, usageTrendChart } from "../components/charts.js";
import { bindPagination, bindResponsiveCollection, compareNullable, paginate, paginationMarkup } from "../components/collection.js";
import { chips, escapeHtml, measurementContent, moduleMessage, setBusy } from "../components/dom.js";
import { renderFreshnessBanner } from "../components/freshnessBanner.js";
import { renderJapanMap } from "../components/japanMap.js";
import { renderProductMatrix } from "../components/productMatrix.js";
import {
  displayCount, displayDateTime, displayMeasuredDuration, displayMeasuredRate,
  displayRate, measurementCoverage, measurementStateLabel,
} from "../viewModels/formatters.js";

const OVERVIEW_MODULES = ["kpis", "environment", "usage", "tasks", "activity", "products"];

function periodLabel(preset) {
  return {
    today: "今日", last_7d: "過去7日", last_14d: "過去14日",
    last_30d: "過去30日", last_60d: "過去60日", all: "全期間",
  }[preset] || "選択期間";
}

function coverageNote(measurement) {
  return `<span class="measurementBadge ${escapeHtml(measurement.measurementState)}">${escapeHtml(measurementStateLabel(measurement))}</span><small>${escapeHtml(measurementCoverage(measurement))}</small>`;
}

export class OverviewPage {
  constructor(root, { navigate, getPreset, state, signal, isCurrent, setArea }) {
    this.root = root;
    this.navigate = navigate;
    this.getPreset = getPreset;
    this.areaKey = state.area;
    this.signal = signal;
    this.isCurrent = isCurrent;
    this.setArea = setArea;
    this.query = state.overviewQuery;
    this.activity = state.overviewActivity;
    this.sort = state.overviewSort;
    this.page = state.overviewPage;
    this.userModel = null;
    this.compactCollection = bindResponsiveCollection(this.signal, () => this.renderUserRows());
  }

  async load() {
    setBusy(this.root, true);
    this.root.innerHTML = this.shell();
    await Promise.allSettled([this.loadOverview(), this.loadRegions(), this.loadUsers()]);
    if (this.isCurrent()) setBusy(this.root, false);
  }

  shell() {
    return `
      <div class="pageHeading overviewHeading">
        <div><p class="eyebrow">利用状況・定着・ニーズ</p><h2>全体サマリー</h2><p>${escapeHtml(periodLabel(this.getPreset()))}の利用実態を、採用から個人まで順に確認できます。</p></div>
        <div class="scopeSummary"><span data-global-count>主要分析対象を読込中</span><span data-user-count>ユーザー・地域対象を読込中</span><div id="areaChip"></div></div>
      </div>
      <div class="freshnessBanner" data-freshness-banner data-state="loading">更新状況を確認中です。</div>
      <section class="panel priorityPanel" data-module="kpis"><div class="panelHead"><div><p class="sectionIndex">01</p><h3>主要KPI</h3></div><small>MR・ヘルスケア本社</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="panel" data-module="environment"><div class="panelHead"><div><p class="sectionIndex">02</p><h3>利用環境・モード</h3></div><small>補助的な利用状況</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="twoGrid insightGrid"><article class="panel" data-module="usage"><div class="panelHead"><div><p class="sectionIndex">03</p><h3>利用推移</h3></div></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article><article class="panel" data-module="tasks"><div class="panelHead"><div><p class="sectionIndex">03</p><h3>質問種類</h3></div><small>ユーザーが何をしたいか</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article></section>
      <section class="panel" data-module="activity"><div class="panelHead"><div><p class="sectionIndex">04</p><h3>活性度分布</h3></div><small>直近14日の活躍日数で判定</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="panel" data-module="users"><div class="panelHead"><div><p class="sectionIndex">05</p><h3>ユーザー一覧</h3></div><small>最終利用は全期間、利用日・メッセージは直近7日</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>
      <section class="twoGrid mapGrid"><article class="panel" data-module="map"><div class="panelHead"><div><p class="sectionIndex">06</p><h3>日本利用マップ</h3></div><small>色の濃さ: 利用率</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article><article class="panel" data-module="ranking"><div class="panelHead"><h3>地域ランキング</h3><small>利用率順</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></article></section>
      <section class="panel" data-module="products"><div class="panelHead"><div><p class="sectionIndex">07</p><h3>製品ニーズ</h3></div><small>製品 Top 10 × 質問種類</small></div><div data-module-body>${moduleMessage("読み込み中…", "loading")}</div></section>`;
  }

  body(name) { return this.root.querySelector(`[data-module="${name}"] [data-module-body]`); }

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
      this.root.querySelector("[data-global-count]").textContent = envelope.scopeUserCount == null
        ? "主要分析対象人数を確認できません"
        : `主要分析対象 ${envelope.scopeUserCount}名`;
      renderFreshnessBanner(
        this.root.querySelector("[data-freshness-banner]"),
        envelope.freshness,
        envelope.analyticsQuality,
        envelope.metadataIssues,
      );
      this.renderPart("kpis", () => this.renderKpis(kpisModel(raw)));
      this.renderPart("environment", () => this.renderEnvironment(environmentModel(raw)));
      this.renderPart("usage", () => this.renderUsage(usageTrendModel(raw)));
      this.renderPart("tasks", () => this.renderTasks(taskModel(raw)));
      this.renderPart("activity", () => this.renderActivity(activityModel(raw)));
      this.renderPart("products", () => this.renderProducts(productsModel(raw)));
    } catch (error) {
      if (isCancellation(error)) throw error;
      OVERVIEW_MODULES.forEach((name) => this.fail(name, error));
    }
  }

  renderKpis(model) {
    const activeLabel = this.getPreset() === "today" ? "DAU" : "期間利用者";
    const cards = [
      { label: activeLabel, value: displayCount(model.activeUsers), unit: "人" },
      { label: "利用率", value: displayRate(model.adoptionRate) },
      { label: "再訪率", value: displayRate(model.returnRate), note: model.returnRate == null && this.getPreset() === "today" ? "単日では定義しません" : "2日以上利用した割合" },
      { label: "1人あたり質問", value: model.questionsPerActiveUser == null ? "-" : Number(model.questionsPerActiveUser).toFixed(1), unit: "件" },
      { label: "回答成功率（完全交付）", value: displayMeasuredRate(model.completeDelivery), measurement: model.completeDelivery },
      { label: "P95応答時間", value: displayMeasuredDuration(model.p95Latency), measurement: model.p95Latency },
    ];
    this.body("kpis").innerHTML = `<div id="kpis" class="kpiGrid">${cards.map((card) => `<article class="kpiCard ${card.measurement ? `measurementCard ${escapeHtml(card.measurement.measurementState)}` : ""}"><span>${escapeHtml(card.label)}</span><div><strong class="${String(card.value).length > 9 ? "textValue" : ""}">${escapeHtml(card.value)}</strong>${card.unit ? `<small>${escapeHtml(card.unit)}</small>` : ""}</div>${card.measurement ? coverageNote(card.measurement) : card.note ? `<small class="kpiNote">${escapeHtml(card.note)}</small>` : ""}</article>`).join("")}</div>`;
  }

  renderEnvironment(model) {
    const body = this.body("environment");
    const dimension = (measurement, canvasId, missingLabel) => measurementContent(measurement, {
      content: `<div class="chartBox compactChart"><canvas id="${canvasId}"></canvas></div>`,
      notMeasuredMessage: `履歴未計測：この期間の${missingLabel}は記録されていません。`,
      partialMessage: `一部計測: ${measurementCoverage(measurement)}`,
    });
    body.innerHTML = `<div class="threeGrid"><article class="subPanel"><h4>時間帯別質問</h4><div class="chartBox compactChart"><canvas id="hourChart"></canvas></div></article><article class="subPanel"><h4>デバイス</h4>${dimension(model.deviceMeasurement, "deviceChart", "デバイス")}</article><article class="subPanel"><h4>利用モード</h4>${dimension(model.modeMeasurement, "modeChart", "利用モード")}</article></div>`;
    barChart(body.querySelector("#hourChart"), model.hourlyQuestions, { label: "質問数", summary: "時間帯別の質問数" });
    if (body.querySelector("#deviceChart")) doughnutChart(body.querySelector("#deviceChart"), model.deviceDistribution, { summary: "デバイス別の利用構成" });
    if (body.querySelector("#modeChart")) doughnutChart(body.querySelector("#modeChart"), model.modeDistribution, { summary: "利用モード別の構成" });
  }

  renderUsage(rows) {
    const body = this.body("usage");
    const partial = rows.find((row) => row.isPartial);
    body.innerHTML = `<div class="chartBox primaryChart"><canvas id="usageChart"></canvas></div>${partial ? `<p class="measurementNote">${escapeHtml(partial.date)} は反映済み時刻までの途中集計です。</p>` : ""}`;
    usageTrendChart(body.querySelector("#usageChart"), rows);
  }

  renderTasks(model) {
    const body = this.body("tasks");
    if (model.measurement.measurementState === "no_usage") { body.innerHTML = moduleMessage("この期間に利用記録はありません。", "empty"); return; }
    if (model.measurement.measurementState === "not_measured") { body.innerHTML = moduleMessage("この期間の質問種類は履歴に記録されていません。", "not_measured"); return; }
    body.innerHTML = `<div class="chartBox primaryChart"><canvas id="taskChart"></canvas></div>${model.measurement.measurementState === "partial" ? `<p class="measurementNote">${coverageNote(model.measurement)}</p>` : ""}`;
    barChart(body.querySelector("#taskChart"), model.rows, { horizontal: true, label: "質問数", color: "#2fd5c4", summary: "質問種類別の質問数" });
  }

  renderActivity(model) {
    const body = this.body("activity");
    body.innerHTML = '<div class="activityGrid"><article><h4>全体構成</h4><div class="chartBox donut"><canvas id="activityChart"></canvas></div></article><article><h4>地域別構成</h4><div class="chartBox"><canvas id="areaActivityChart"></canvas></div></article><article><h4>役割別構成</h4><div class="chartBox"><canvas id="roleActivityChart"></canvas></div></article></div>';
    doughnutChart(body.querySelector("#activityChart"), model.distribution, { summary: "全体の活性度構成" });
    stackedChart(body.querySelector("#areaActivityChart"), model.byArea, { summary: "地域別の活性度構成" });
    stackedChart(body.querySelector("#roleActivityChart"), model.byRole, { summary: "役割別の活性度構成" });
  }

  renderProducts(model) {
    const body = this.body("products");
    if (model.resolution.measurementState === "no_usage") { body.innerHTML = moduleMessage("この期間に利用記録はありません。", "empty"); return; }
    if (model.resolution.measurementState === "not_measured") { body.innerHTML = moduleMessage("この期間の製品項目は履歴に記録されていません。", "not_measured"); return; }
    const unresolved = Number.isInteger(model.resolution.unresolvedQuestions) ? model.resolution.unresolvedQuestions : 0;
    body.innerHTML = `<div class="twoGrid productGrid"><article class="subPanel"><h4>質問対象（製品）Top 10</h4><div class="chartBox primaryChart"><canvas id="productChart"></canvas></div></article><article class="subPanel matrixPanel"><h4>製品 × 質問種類</h4><div id="productMatrix" class="productMatrix"></div></article></div>${model.resolution.measurementState === "partial" ? `<p class="measurementNote">${coverageNote(model.resolution)}</p>` : ""}${unresolved ? `<p class="measurementNote">正式な製品名を確認できなかった質問 ${displayCount(unresolved)}件は、ランキングとマトリクスに含めていません。</p>` : ""}`;
    barChart(body.querySelector("#productChart"), model.topProducts, { horizontal: true, label: "質問数", color: "#7f88ff", summary: "質問対象製品の上位10件" });
    renderProductMatrix(body.querySelector("#productMatrix"), model.matrix);
  }

  async loadRegions() {
    try {
      const model = regionsModel(await getRegions({ preset: this.getPreset() }, { signal: this.signal }));
      if (!this.isCurrent()) return;
      this.root.querySelector("[data-user-count]").textContent = model.scopeUserCount == null
        ? "ユーザー・地域対象人数を確認できません"
        : `ユーザー・地域対象 ${model.scopeUserCount}名`;
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
    body.innerHTML = `<div id="regionRanking" class="rankingList">${rows.map((row, index) => `<button class="rankingRow ${row.areaKey === this.areaKey ? "isSelected" : ""}" data-area="${escapeHtml(row.areaKey)}" aria-pressed="${row.areaKey === this.areaKey}"><b>${index + 1}</b><span class="rankingLabel"><strong>${escapeHtml(row.area)}</strong><small>${displayCount(row.activeUsers)} / ${displayCount(row.rosterUsers)}人 · ${displayCount(row.questions)}件</small></span><span class="rankingValue">${displayRate(row.adoptionRate)}</span><i class="rankingBar" style="--bar:${Math.max(0, Math.min(100, Number(row.adoptionRate || 0) * 100))}%"></i></button>`).join("") || moduleMessage("この期間に地域利用はありません。", "empty")}</div>${issues.length ? `<p class="measurementNote">${displayCount(issues.length)}件の不正な地域行を表示対象から外しました。</p>` : ""}`;
    body.querySelectorAll(".rankingRow").forEach((button) => button.addEventListener("click", () => this.setArea(button.dataset.area === this.areaKey ? "" : button.dataset.area)));
  }

  async renderMap(rows) {
    const body = this.body("map");
    body.innerHTML = '<div id="japanMap" class="mapCanvas"></div><div class="mapLegend" aria-label="地図の色の説明"><span>低い</span><i></i><span>高い</span></div>';
    await renderJapanMap(body.querySelector("#japanMap"), rows, { selectedAreaKey: this.areaKey, onSelect: (key) => this.setArea(key === this.areaKey ? "" : key), signal: this.signal });
  }

  renderAreaChip(rows) {
    const selected = rows.find((row) => row.areaKey === this.areaKey);
    this.root.querySelector("#areaChip").innerHTML = selected ? `<button class="filterChip" id="clearArea" aria-label="${escapeHtml(selected.area)}の絞り込みを解除">${escapeHtml(selected.area)} <span aria-hidden="true">×</span></button>` : "";
    this.root.querySelector("#clearArea")?.addEventListener("click", () => this.setArea(""));
  }

  async loadUsers() {
    try {
      this.userModel = usersModel(await getUsers({ preset: this.getPreset(), area_key: this.areaKey }, { signal: this.signal }));
      if (!this.isCurrent()) return;
      const body = this.body("users");
      body.innerHTML = `<div class="collectionToolbar"><label>ユーザー検索<input id="overviewUserSearch" type="search" value="${escapeHtml(this.query)}" placeholder="氏名またはメール"></label><label>活性度<select id="overviewActivity"><option value="">すべて</option><option value="high" ${this.activity === "high" ? "selected" : ""}>高</option><option value="middle" ${this.activity === "middle" ? "selected" : ""}>中</option><option value="low" ${this.activity === "low" ? "selected" : ""}>低</option><option value="dormant" ${this.activity === "dormant" ? "selected" : ""}>休眠</option></select></label><label>並び順<select id="overviewSort"><option value="last_desc" ${this.sort === "last_desc" ? "selected" : ""}>最終利用が新しい順</option><option value="name_asc" ${this.sort === "name_asc" ? "selected" : ""}>社員名順</option><option value="messages_desc" ${this.sort === "messages_desc" ? "selected" : ""}>メッセージ数順</option><option value="success_desc" ${this.sort === "success_desc" ? "selected" : ""}>回答成功率順</option></select></label></div><div id="overviewUserResults"></div>`;
      body.querySelector("#overviewUserSearch").addEventListener("input", (event) => this.updateUserCollection({ query: event.target.value, page: 1 }));
      body.querySelector("#overviewActivity").addEventListener("change", (event) => this.updateUserCollection({ activity: event.target.value, page: 1 }));
      body.querySelector("#overviewSort").addEventListener("change", (event) => this.updateUserCollection({ sort: event.target.value, page: 1 }));
      this.renderUserRows();
    } catch (error) {
      if (isCancellation(error)) throw error;
      this.fail("users", error);
    }
  }

  updateUserCollection({ query = this.query, activity = this.activity, sort = this.sort, page = this.page }) {
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
    const query = this.query.trim().toLocaleLowerCase("ja-JP");
    const rows = this.userModel.users.filter((row) => {
      const matchesQuery = !query || `${row.name} ${row.email}`.toLocaleLowerCase("ja-JP").includes(query);
      return matchesQuery && (!this.activity || row.activity === this.activity);
    });
    const sorters = {
      name_asc: (a, b) => compareNullable(a.name, b.name, "asc"),
      messages_desc: (a, b) => compareNullable(a.userMessageCount7, b.userMessageCount7),
      success_desc: (a, b) => compareNullable(a.completeDelivery.value, b.completeDelivery.value),
      last_desc: (a, b) => compareNullable(a.lastActiveAt, b.lastActiveAt),
    };
    rows.sort(sorters[this.sort] || sorters.last_desc);
    const page = paginate(rows, this.page, this.compactCollection.matches ? 6 : 15);
    if (page.page !== this.page) this.page = page.page;
    const rowHtml = page.items.map((row) => `<tr><td><strong>${escapeHtml(row.name)}</strong>${row.labels.length ? `<div class="chips">${chips(row.labels)}</div>` : ""}</td><td>${escapeHtml(row.email)}</td><td>${escapeHtml(row.area)}</td><td>${displayDateTime(row.lastActiveAt)}</td><td>${displayCount(row.activeDays7)}</td><td>${displayCount(row.userMessageCount7)}</td><td><span title="${escapeHtml(measurementCoverage(row.completeDelivery))}">${displayMeasuredRate(row.completeDelivery)}</span></td><td><span class="activityBadge ${escapeHtml(row.activity || "unknown")}">${escapeHtml(row.activityLabel)}</span></td><td><button class="linkButton" data-roster="${escapeHtml(row.rosterId)}">詳細</button></td></tr>`).join("");
    const cardHtml = page.items.map((row) => `<article class="userCard"><header><div><strong>${escapeHtml(row.name)}</strong><small>${escapeHtml(row.email)}</small></div><span class="activityBadge ${escapeHtml(row.activity || "unknown")}">${escapeHtml(row.activityLabel)}</span></header><dl><div><dt>エリア</dt><dd>${escapeHtml(row.area)}</dd></div><div><dt>最終利用</dt><dd>${displayDateTime(row.lastActiveAt)}</dd></div><div><dt>7日利用</dt><dd>${displayCount(row.activeDays7)}日</dd></div><div><dt>7日消息</dt><dd>${displayCount(row.userMessageCount7)}件</dd></div><div><dt>完全交付</dt><dd>${displayMeasuredRate(row.completeDelivery)}</dd></div></dl><button class="linkButton" data-roster="${escapeHtml(row.rosterId)}">詳細を見る</button></article>`).join("");
    const issueNote = this.userModel.issues.length
      ? `<p class="measurementNote">ユーザーデータの契約上の欠落を ${displayCount(this.userModel.issues.length)}件検出しました。該当値は「未測定」で表示し、安全に表示できない行だけを除外しています。</p>`
      : "";
    target.innerHTML = (page.total ? `<div class="desktopTable"><div class="tableScroll" tabindex="0" aria-label="ユーザー一覧"><table id="overviewUsers"><caption>管理者を除くユーザーの利用状況</caption><thead><tr><th>社員名</th><th>メール</th><th>エリア</th><th>最終利用</th><th>直近7日利用日数</th><th>直近7日消息数</th><th>回答成功率</th><th>活性度</th><th></th></tr></thead><tbody>${rowHtml}</tbody></table></div></div><div class="mobileCards">${cardHtml}</div>${paginationMarkup(page)}` : moduleMessage("条件に一致するユーザーはいません。", "empty")) + issueNote;
    target.querySelectorAll("[data-roster]").forEach((button) => button.addEventListener("click", () => this.navigate("user", { roster: button.dataset.roster })));
    bindPagination(target, page, (next) => this.updateUserCollection({ page: next }));
  }
}
