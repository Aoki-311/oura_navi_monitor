function requiredArray(payload, key, label) {
  if (!Array.isArray(payload?.[key])) throw new Error(`${label}を表示できません。`);
  return payload[key];
}

function requiredObject(payload, key, label) {
  const value = payload?.[key];
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label}を表示できません。`);
  return value;
}

function requiredCount(value, label) {
  if (!Number.isInteger(value) || value < 0) throw new Error(`${label}の件数を確認できません。`);
  return value;
}

function nullableNumber(value, label) {
  if (value == null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${label}の数値を確認できません。`);
  return value;
}

function requiredText(value, label) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${label}を確認できません。`);
  return value;
}

export function measurementModel(value, { latency = false } = {}) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("計測範囲を確認できません。");
  const measuredCount = requiredCount(value.measuredCount, "計測済み");
  const totalCount = requiredCount(value.totalCount, "対象");
  if (measuredCount > totalCount) throw new Error("計測範囲が不正です。");
  const expectedState = totalCount === 0 ? "no_usage" : measuredCount === 0 ? "not_measured" : measuredCount < totalCount ? "partial" : "measured";
  if (value.measurementState !== expectedState) throw new Error("計測状態が件数と一致しません。");
  return {
    [latency ? "valueMs" : "value"]: nullableNumber(value[latency ? "valueMs" : "value"], latency ? "応答時間" : "割合"),
    measuredCount,
    totalCount,
    measurementState: expectedState,
  };
}

export function coverageModel(value, label = "計測範囲") {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label}を確認できません。`);
  const measuredCount = requiredCount(value.measuredCount, `${label}の計測済み`);
  const totalCount = requiredCount(value.totalCount, `${label}の対象`);
  if (measuredCount > totalCount) throw new Error(`${label}が不正です。`);
  const expectedState = totalCount === 0 ? "no_usage" : measuredCount === 0 ? "not_measured" : measuredCount < totalCount ? "partial" : "measured";
  if (value.measurementState !== expectedState) throw new Error(`${label}の状態が件数と一致しません。`);
  return { measuredCount, totalCount, measurementState: expectedState };
}

function envelope(payload, scope) {
  if (!payload || (scope && payload.scope !== scope)) throw new Error("分析データの形式が不正です。");
  const freshness = requiredObject(payload, "freshness", "データ更新情報");
  if (!["fresh", "stale", "unknown"].includes(freshness.state) || typeof freshness.dataThrough !== "string") throw new Error("データ更新情報を確認できません。");
  return payload;
}

export function overviewEnvelope(payload) {
  envelope(payload, "global");
  return {
    payload,
    scopeUserCount: requiredCount(payload.scopeUserCount, "全体対象者"),
    freshness: payload.freshness,
  };
}

export function kpisModel(payload) {
  const kpis = requiredObject(payload, "kpis", "主要KPI");
  return {
    activeUsers: requiredCount(kpis.activeUsers, "利用者"),
    adoptionRate: nullableNumber(kpis.adoptionRate, "利用率"),
    returnRate: nullableNumber(kpis.returnRate, "再訪率"),
    questionsPerActiveUser: nullableNumber(kpis.questionsPerActiveUser, "1人あたり質問"),
    completeDelivery: measurementModel(kpis.completeDelivery),
    p95Latency: measurementModel(kpis.p95Latency, { latency: true }),
  };
}

function distributionRows(rows, label) {
  return rows.map((row) => ({
    key: typeof row?.key === "string" ? row.key : "",
    label: requiredText(row?.label, `${label}の名称`),
    count: requiredCount(row?.count, label),
    rate: nullableNumber(row?.rate, label),
  }));
}

export function environmentModel(payload) {
  return {
    hourlyQuestions: requiredArray(payload, "hourlyQuestions", "時間帯別質問").map((row) => ({
      label: String(row?.hour || ""), count: requiredCount(row?.count, "時間帯別質問"),
    })),
    deviceDistribution: distributionRows(requiredArray(payload, "deviceDistribution", "デバイス分析"), "デバイス分析"),
    deviceMeasurement: coverageModel(payload.deviceMeasurement, "デバイス分析の計測範囲"),
    modeDistribution: distributionRows(requiredArray(payload, "modeDistribution", "モード分析"), "モード分析"),
    modeMeasurement: coverageModel(payload.modeMeasurement, "モード分析の計測範囲"),
  };
}

export function usageTrendModel(payload) {
  return requiredArray(payload, "usageTrend", "利用推移").map((row) => ({
    date: String(row?.date || ""),
    activeUsers: requiredCount(row?.activeUsers, "利用推移"),
    questions: requiredCount(row?.questions, "利用推移"),
  }));
}

export function taskModel(payload) {
  return {
    rows: distributionRows(requiredArray(payload, "requestTasks", "質問種類"), "質問種類"),
    measurement: coverageModel(payload.taskMeasurement, "質問種類の計測範囲"),
  };
}

export function activityModel(payload) {
  return {
    distribution: distributionRows(requiredArray(payload, "activityDistribution", "活性度分布"), "活性度分布"),
    byArea: requiredArray(payload, "activityByArea", "地域別活性度"),
    byRole: requiredArray(payload, "activityByRole", "役割別活性度"),
  };
}

export function productsModel(payload) {
  return {
    topProducts: requiredArray(payload, "topProducts", "製品ランキング").map((row) => ({ label: String(row?.label || ""), count: requiredCount(row?.count, "製品ランキング") })),
    matrix: requiredArray(payload, "productTaskMatrix", "製品マトリクス").map((row) => ({
      product: requiredText(row?.product, "製品名"),
      task: requiredText(row?.task, "質問種類"),
      taskLabel: requiredText(row?.taskLabel, "質問種類名"),
      count: requiredCount(row?.count, "製品マトリクス"),
    })),
    resolution: {
      ...requiredObject(payload, "productResolution", "製品判定範囲"),
      ...coverageModel(payload.productResolution, "製品判定範囲"),
    },
  };
}

export function regionsModel(payload) {
  envelope(payload);
  if (!Number.isInteger(payload.scopeUserCount) || payload.scopeUserCount < 0 || !Array.isArray(payload.regions)) throw new Error("地域データの形式が不正です。");
  const issues = [];
  const regions = payload.regions.flatMap((row, index) => {
    try {
      const areaKey = typeof row?.areaKey === "string" && row.areaKey.trim() ? row.areaKey : null;
      const area = typeof row?.area === "string" && row.area.trim() ? row.area : null;
      if (!areaKey || !area) throw new Error("地域名を確認できません。");
      return [{
        areaKey, area,
        rosterUsers: requiredCount(row.rosterUsers, "地域対象者"),
        activeUsers: requiredCount(row.activeUsers, "地域利用者"),
        questions: requiredCount(row.questions, "地域質問"),
        adoptionRate: nullableNumber(row.adoptionRate, "地域利用率"),
        returnRate: nullableNumber(row.returnRate, "地域再訪率"),
      }];
    } catch (error) {
      issues.push(`${index + 1}行目: ${error.message}`);
      return [];
    }
  });
  return { scopeUserCount: payload.scopeUserCount, freshness: payload.freshness, regions, issues };
}
