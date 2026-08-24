import { getOverview, getRegions, getUsers } from "../api/client.js";
import { overviewModel, regionsModel } from "../adapters/overviewAdapter.js";
import { usersModel } from "../adapters/usersAdapter.js";
import { barChart, doughnutChart, stackedChart, usageTrendChart } from "../components/charts.js";
import { chips, escapeHtml, moduleMessage, setBusy } from "../components/dom.js";
import { renderJapanMap } from "../components/japanMap.js";
import { renderProductMatrix } from "../components/productMatrix.js";
import { displayCount, displayDateTime, displayMs, displayRate } from "../viewModels/formatters.js";

export class OverviewPage {
  constructor(root, { navigate, toast, getPreset }) {
    this.root = root;
    this.navigate = navigate;
    this.toast = toast;
    this.getPreset = getPreset;
    this.areaKey = "";
  }

  async load() {
    setBusy(this.root, true);
    this.root.innerHTML = this.shell();
    const params = { preset: this.getPreset(), area_key: this.areaKey };
    const regionParams = { preset: this.getPreset() };
    const results = await Promise.allSettled([getOverview(params), getRegions(regionParams), getUsers(params)]);
    const [overviewResult, regionsResult, usersResult] = results;
    if (overviewResult.status === "fulfilled") {
      try { this.renderOverview(overviewModel(overviewResult.value)); } catch (error) { this.failGroup("overviewModules", error); }
    } else this.failGroup("overviewModules", overviewResult.reason);
    if (regionsResult.status === "fulfilled") {
      try { await this.renderRegions(regionsModel(regionsResult.value)); } catch (error) { this.failGroup("regionModules", error); }
    } else this.failGroup("regionModules", regionsResult.reason);
    if (usersResult.status === "fulfilled") {
      try { this.renderUsers(usersModel(usersResult.value)); } catch (error) { this.failGroup("userModules", error); }
    } else this.failGroup("userModules", usersResult.reason);
    setBusy(this.root, false);
  }

  shell() {
    return `
      <div class="pageHeading"><div><p class="eyebrow">69名の全体指標 / 80名のユーザー・地域分析</p><h2>全体サマリー</h2><p>利用の広がり、回答の完全交付、地域差と製品ニーズを一画面で確認します。</p></div><div id="areaChip"></div></div>
      <section class="panel" data-group="overviewModules"><div class="panelHead"><h3><span>01</span>主要KPI</h3><small>対象: MR・ヘルスケア本社</small></div><div id="kpis" class="kpiGrid"></div><p id="kpiNote" class="measurementNote" hidden></p></section>
      <section class="panel" data-group="overviewModules"><div class="panelHead"><h3><span>02</span>利用環境・モード</h3></div><div class="threeGrid"><article class="subPanel"><h4>時間帯別質問</h4><div class="chartBox"><canvas id="hourChart"></canvas></div></article><article class="subPanel"><h4>デバイス</h4><div class="chartBox"><canvas id="deviceChart"></canvas></div></article><article class="subPanel"><h4>利用モード</h4><div class="chartBox"><canvas id="modeChart"></canvas></div></article></div></section>
      <section class="twoGrid" data-group="overviewModules"><article class="panel"><div class="panelHead"><h3><span>03</span>利用推移</h3></div><div class="chartBox tall"><canvas id="usageChart"></canvas></div></article><article class="panel"><div class="panelHead"><h3>質問タイプ</h3></div><div class="chartBox tall"><canvas id="categoryChart"></canvas></div></article></section>
      <section class="panel" data-group="overviewModules"><div class="panelHead"><h3><span>04</span>活性度分布</h3></div><div class="activityGrid"><div class="chartBox donut"><canvas id="activityChart"></canvas></div><div><h4>地域別構成</h4><div class="chartBox"><canvas id="areaActivityChart"></canvas></div></div><div><h4>役割別構成</h4><div class="chartBox"><canvas id="roleActivityChart"></canvas></div></div></div></section>
      <section class="panel" data-group="userModules"><div class="panelHead"><h3><span>05</span>ユーザー一覧</h3><small>対象: 管理者を除く80名</small></div><div class="tableScroll"><table id="overviewUsers"><thead><tr><th>社員名</th><th>メール</th><th>エリア</th><th>最終利用</th><th>直近7日利用日数</th><th>直近7日メッセージ数</th><th>回答成功率</th><th>活性度</th><th></th></tr></thead><tbody></tbody></table></div></section>
      <section class="twoGrid mapGrid" data-group="regionModules"><article class="panel"><div class="panelHead"><h3><span>06</span>日本利用マップ</h3><small>対象: 管理者を除く80名</small></div><div id="japanMap" class="mapCanvas"></div></article><article class="panel"><div class="panelHead"><h3>地域ランキング</h3></div><div id="regionRanking" class="rankingList"></div></article></section>
      <section class="panel" data-group="overviewModules"><div class="panelHead"><h3><span>07</span>製品ニーズ</h3></div><div class="twoGrid productGrid"><article class="subPanel"><h4>質問対象（製品）Top 10</h4><div class="chartBox tall"><canvas id="productChart"></canvas></div></article><article class="subPanel matrixPanel"><h4>製品 × 質問タイプ</h4><div id="productMatrix" class="productMatrix"></div></article></div><p id="productNote" class="measurementNote" hidden></p></section>`;
  }

  renderOverview(model) {
    if (model.status === "unavailable") {
      this.failGroup("overviewModules", new Error("集計データが最新ではありません"));
      return;
    }
    const kpis = [
      ["期間利用者数", displayCount(model.kpis.activeUsers), "人"],
      ["利用率", displayRate(model.kpis.adoptionRate), ""],
      ["再訪率", displayRate(model.kpis.returnRate), ""],
      ["1人あたり質問", model.kpis.questionsPerActiveUser == null ? "-" : Number(model.kpis.questionsPerActiveUser).toFixed(1), "件"],
      ["回答成功率（完全交付）", displayRate(model.kpis.completeDeliveryRate), ""],
      ["P95応答時間", displayMs(model.kpis.p95LatencyMs), ""],
    ];
    this.root.querySelector("#kpis").innerHTML = kpis.map(([label, value, unit]) => `<article class="kpiCard"><span>${label}</span><strong>${value}</strong><small>${unit}</small></article>`).join("");
    const note = this.root.querySelector("#kpiNote");
    const missingMeasurement = model.kpis.completeDeliveryRate == null || model.kpis.p95LatencyMs == null;
    note.hidden = !missingMeasurement;
    note.textContent = missingMeasurement
      ? "「-」は、この期間の回答について完全交付または応答時間の計測が揃っていないことを示します。"
      : "";
    barChart(this.root.querySelector("#hourChart"), model.hourlyQuestions.map((row) => ({ label: row.hour, count: row.count })), { label: "質問数" });
    doughnutChart(this.root.querySelector("#deviceChart"), model.deviceDistribution);
    doughnutChart(this.root.querySelector("#modeChart"), model.modeDistribution);
    usageTrendChart(this.root.querySelector("#usageChart"), model.usageTrend);
    barChart(this.root.querySelector("#categoryChart"), model.questionCategories, { horizontal: true, label: "質問数", color: "#27d9d2" });
    doughnutChart(this.root.querySelector("#activityChart"), model.activityDistribution);
    stackedChart(this.root.querySelector("#areaActivityChart"), model.activityByArea);
    stackedChart(this.root.querySelector("#roleActivityChart"), model.activityByRole);
    barChart(this.root.querySelector("#productChart"), model.topProducts, { horizontal: true, label: "質問数", color: "#8f72ff" });
    renderProductMatrix(this.root.querySelector("#productMatrix"), model.productQuestionMatrix);
    const productNote = this.root.querySelector("#productNote");
    const unresolvedProducts = Number(model.productResolution.unresolvedQuestions || 0);
    productNote.hidden = unresolvedProducts === 0;
    productNote.textContent = unresolvedProducts
      ? `正式な製品名を確認できなかった質問 ${unresolvedProducts}件は、製品ランキングとマトリクスに含めていません。`
      : "";
  }

  async renderRegions(model) {
    if (model.status === "unavailable") throw new Error("地域データが最新ではありません");
    await renderJapanMap(this.root.querySelector("#japanMap"), model.regions, {
      selectedAreaKey: this.areaKey,
      onSelect: async (key) => { this.areaKey = key === this.areaKey ? "" : key; await this.load(); },
    });
    this.root.querySelector("#regionRanking").innerHTML = model.regions.map((row, index) => `
      <button class="rankingRow ${row.areaKey === this.areaKey ? "isSelected" : ""}" data-area="${escapeHtml(row.areaKey)}"><b>${index + 1}</b><span><strong>${escapeHtml(row.area)}</strong><small>${displayCount(row.activeUsers)}人 / ${displayCount(row.questions)}件</small></span><em>${displayRate(row.adoptionRate)}</em></button>`).join("") || moduleMessage("地域データはありません");
    this.root.querySelectorAll(".rankingRow").forEach((button) => button.addEventListener("click", async () => { this.areaKey = button.dataset.area === this.areaKey ? "" : button.dataset.area; await this.load(); }));
    const selected = model.regions.find((row) => row.areaKey === this.areaKey);
    this.root.querySelector("#areaChip").innerHTML = selected ? `<button class="filterChip" id="clearArea">${escapeHtml(selected.area)} <span>×</span></button>` : "";
    this.root.querySelector("#clearArea")?.addEventListener("click", async () => { this.areaKey = ""; await this.load(); });
  }

  renderUsers(model) {
    if (model.status === "unavailable") throw new Error("ユーザーデータが最新ではありません");
    const body = this.root.querySelector("#overviewUsers tbody");
    body.innerHTML = model.users.map((row) => `<tr><td><strong>${escapeHtml(row.name)}</strong><div class="chips">${chips(row.labels)}</div></td><td>${escapeHtml(row.email)}</td><td>${escapeHtml(row.area)}</td><td>${displayDateTime(row.lastActiveAt)}</td><td>${displayCount(row.activeDays7)}</td><td>${displayCount(row.questionCount7)}</td><td>${displayRate(row.completeDeliveryRate)}</td><td><span class="activityBadge ${escapeHtml(row.activity)}">${escapeHtml(row.activityLabel)}</span></td><td><button class="linkButton" data-roster="${escapeHtml(row.rosterId)}">詳細</button></td></tr>`).join("") || `<tr><td colspan="9">${moduleMessage("対象ユーザーはいません")}</td></tr>`;
    body.querySelectorAll("[data-roster]").forEach((button) => button.addEventListener("click", () => this.navigate("user", { roster: button.dataset.roster })));
  }

  failGroup(group, error) {
    this.root.querySelectorAll(`[data-group="${group}"]`).forEach((section) => { section.querySelector(":scope > .moduleMessage")?.remove(); section.insertAdjacentHTML("beforeend", moduleMessage(error?.message || "データ取得不可")); });
    this.toast(error?.message || "一部データを取得できませんでした", "error");
  }
}
