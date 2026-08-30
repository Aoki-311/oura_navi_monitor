import { contentDiagnosticsModel } from "./contentDiagnosticsAdapter.js";

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

function requiredBoolean(value, label) {
  if (typeof value !== "boolean") throw new Error(`${label}を確認できません。`);
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

const measurementReasons = new Set([
  "complete", "no_usage", "population_without_usage", "historical_unavailable",
  "current_data_gap", "mixed_history_and_current_gap", "mixed_no_usage_and_data_gap",
  "compatibility_unavailable",
]);

function measurementReason(value, expectedState, label) {
  const supplied = value?.measurementReason;
  const reason = supplied == null || supplied === ""
    ? expectedState === "measured"
      ? "complete"
      : expectedState === "no_usage"
        ? "no_usage"
        : "compatibility_unavailable"
    : supplied;
  if (!measurementReasons.has(reason)) throw new Error(`${label}の理由を確認できません。`);
  if (expectedState === "measured" && reason !== "complete") throw new Error(`${label}の状態と理由が一致しません。`);
  if (expectedState === "no_usage" && reason !== "no_usage") throw new Error(`${label}の状態と理由が一致しません。`);
  if (["partial", "not_measured"].includes(expectedState) && ["complete", "no_usage"].includes(reason)) throw new Error(`${label}の状態と理由が一致しません。`);
  return reason;
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
    measurementReason: measurementReason(value, expectedState, "計測範囲"),
  };
}

export function coverageModel(value, label = "計測範囲") {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label}を確認できません。`);
  const measuredCount = requiredCount(value.measuredCount, `${label}の計測済み`);
  const totalCount = requiredCount(value.totalCount, `${label}の対象`);
  if (measuredCount > totalCount) throw new Error(`${label}が不正です。`);
  const expectedState = totalCount === 0 ? "no_usage" : measuredCount === 0 ? "not_measured" : measuredCount < totalCount ? "partial" : "measured";
  if (value.measurementState !== expectedState) throw new Error(`${label}の状態が件数と一致しません。`);
  return {
    measuredCount,
    totalCount,
    measurementState: expectedState,
    measurementReason: measurementReason(value, expectedState, label),
  };
}

export function freshnessModel(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("データ更新情報を確認できません。");
  if (!["fresh", "stale", "unknown"].includes(value.state) || typeof value.dataThrough !== "string") throw new Error("データ更新情報を確認できません。");
  return { state: value.state, dataThrough: value.dataThrough };
}

export function analyticsQualityModel(value) {
  if (!value || value.contractVersion !== "dashboard_events_v2") throw new Error("分析品質情報を確認できません。");
  const isolatedEventCount = requiredCount(value.isolatedEventCount, "隔離イベント");
  const totalEventCount = requiredCount(value.totalEventCount, "分析対象イベント");
  if (isolatedEventCount > totalEventCount) throw new Error("分析品質の件数が一致しません。");
  const axis = (key, label) => {
    const coverage = coverageModel(value[key], label);
    const isolatedCount = requiredCount(value[key]?.isolatedCount, `${label}の隔離`);
    if (isolatedCount !== coverage.totalCount - coverage.measuredCount) throw new Error(`${label}の隔離件数が一致しません。`);
    return { ...coverage, isolatedCount };
  };
  const source = requiredObject(value, "sourcePipeline", "取込品質");
  const diagnosticsStatus = source.diagnosticsStatus == null ? "available" : source.diagnosticsStatus;
  const diagnosticsErrorCode = source.diagnosticsErrorCode == null ? "" : source.diagnosticsErrorCode;
  if (!["available", "unavailable"].includes(diagnosticsStatus) || typeof diagnosticsErrorCode !== "string") throw new Error("取込品質の診断状態を確認できません。");
  if (!["clean", "degraded", "blocked", "unknown", "unavailable"].includes(source.state) || typeof source.publishedRunId !== "string") throw new Error("取込品質の状態を確認できません。");
  for (const key of ["latestRunId", "latestRunStatus", "latestRunErrorCode", "latestRunFinishedAt"]) {
    if (typeof source[key] !== "string") throw new Error("最新更新の状態を確認できません。");
  }
  if (source.latestRunStatus && !["running", "succeeded", "failed"].includes(source.latestRunStatus)) throw new Error("最新更新の状態を確認できません。");
  const sourcePipeline = {
    publishedRunId: source.publishedRunId,
    latestRunId: source.latestRunId,
    latestRunStatus: source.latestRunStatus,
    latestRunErrorCode: source.latestRunErrorCode,
    latestRunFinishedAt: source.latestRunFinishedAt,
    diagnosticsStatus,
    diagnosticsErrorCode,
    state: source.state,
    quarantinedEventCount: requiredCount(source.quarantinedEventCount, "取込隔離イベント"),
    deduplicatedDeliveryCount: requiredCount(source.deduplicatedDeliveryCount, "重複配信"),
    repairedDuplicateFactCount: requiredCount(source.repairedDuplicateFactCount, "重複ファクト修復"),
    axisUnmeasuredFindingCount: requiredCount(source.axisUnmeasuredFindingCount, "分析軸未計測"),
    batchBlockingFailureCount: requiredCount(source.batchBlockingFailureCount, "公開停止エラー"),
  };
  const expectedSourceState = sourcePipeline.diagnosticsStatus === "unavailable"
    ? "unavailable"
    : sourcePipeline.latestRunStatus === "failed"
    ? "blocked"
    : !sourcePipeline.publishedRunId
      ? "unknown"
      : sourcePipeline.quarantinedEventCount > 0 || sourcePipeline.axisUnmeasuredFindingCount > 0
        ? "degraded"
        : "clean";
  if (sourcePipeline.state !== expectedSourceState) throw new Error("取込品質の状態と件数が一致しません。");
  return {
    contractVersion: value.contractVersion,
    isolatedEventCount,
    totalEventCount,
    classification: axis("classification", "分類品質"),
    task: axis("task", "質問種類品質"),
    product: axis("product", "製品品質"),
    sourcePipeline,
  };
}

function envelope(payload, scope) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("分析データの形式が不正です。");
  if (payload.scope != null && scope && payload.scope !== scope) throw new Error("分析データの対象範囲が一致しません。");
  return payload;
}

export function scopeMetadataModel(payload, expectedScope) {
  envelope(payload, expectedScope);
  const values = [
    payload.scopePolicyVersion, payload.rosterFingerprint, payload.contentFingerprint,
    payload.publishedRunId,
    payload.windowStart, payload.windowEnd, payload.windowTimezone,
  ];
  const windowStartMs = Date.parse(payload.windowStart);
  const windowEndMs = Date.parse(payload.windowEnd);
  const complete = values.every((value) => typeof value === "string" && value.trim())
    && Number.isFinite(windowStartMs)
    && Number.isFinite(windowEndMs)
    && windowStartMs < windowEndMs;
  if (!complete) {
    return {
      scope: expectedScope,
      scopePolicyVersion: "",
      rosterFingerprint: "",
      contentFingerprint: "",
      publishedRunId: "",
      windowStart: "",
      windowEnd: "",
      windowTimezone: "",
      snapshotKey: "",
      available: false,
      issues: ["旧形式のため分析データ版を確認できません。表示内容は保持しますがCSVは利用できません。"],
    };
  }
  const scopePolicyVersion = requiredText(payload.scopePolicyVersion, "分析範囲ポリシー");
  const rosterFingerprint = requiredText(payload.rosterFingerprint, "名簿スナップショット");
  const contentFingerprint = requiredText(payload.contentFingerprint, "表示内容スナップショット");
  const publishedRunId = requiredText(payload.publishedRunId, "公開データ版");
  const windowStart = requiredText(payload.windowStart, "分析開始時刻");
  const windowEnd = requiredText(payload.windowEnd, "分析終了時刻");
  const windowTimezone = requiredText(payload.windowTimezone, "分析タイムゾーン");
  return {
    scope: payload.scope || expectedScope,
    scopePolicyVersion,
    rosterFingerprint,
    contentFingerprint,
    publishedRunId,
    windowStart,
    windowEnd,
    windowTimezone,
    // Every module in one Summary transaction is rendered from the same
    // published roster, label presentation, diagnostics and exact window.
    // Content is part of the receipt because CSV uses it as its immutable
    // export anchor as well.
    snapshotKey: JSON.stringify([
      scopePolicyVersion,
      rosterFingerprint,
      contentFingerprint,
      publishedRunId,
      windowStart,
      windowEnd,
      windowTimezone,
    ]),
    available: true,
    issues: [],
  };
}

export function analyticsMetadataModel(payload, { includeQuality = false } = {}) {
  const metadataIssues = [];
  let freshness = null;
  let analyticsQuality = null;
  try {
    freshness = freshnessModel(payload?.freshness);
  } catch (error) {
    metadataIssues.push(error?.message || "データ更新情報を確認できません。");
  }
  if (includeQuality) {
    try {
      analyticsQuality = analyticsQualityModel(payload?.analyticsQuality);
    } catch (error) {
      metadataIssues.push(error?.message || "分析品質情報を確認できません。");
    }
  }
  return { freshness, analyticsQuality, metadataIssues };
}

export function overviewEnvelope(payload) {
  const scopeMetadata = scopeMetadataModel(payload, "global");
  const metadata = analyticsMetadataModel(payload, { includeQuality: true });
  metadata.metadataIssues.push(...scopeMetadata.issues);
  const contentDiagnostics = contentDiagnosticsModel(payload);
  if (contentDiagnostics.notice) metadata.metadataIssues.push(contentDiagnostics.notice);
  let scopeUserCount = null;
  try {
    scopeUserCount = requiredCount(payload.scopeUserCount, "全体対象者");
  } catch (error) {
    metadata.metadataIssues.push(error?.message || "全体対象者数を確認できません。");
  }
  return {
    payload,
    scopeUserCount,
    scopeMetadata,
    contentDiagnostics,
    ...metadata,
  };
}

export function kpisModel(payload) {
  const kpis = requiredObject(payload, "kpis", "主要KPI");
  const issues = [];
  const safe = (label, create) => {
    try { return create(); } catch (error) {
      issues.push(`${label}: ${error?.message || "確認できません。"}`);
      return null;
    }
  };
  return {
    activeUsers: safe("利用者", () => requiredCount(kpis.activeUsers, "利用者")),
    adoptionRate: safe("利用率", () => nullableNumber(kpis.adoptionRate, "利用率")),
    returnRate: safe("再訪率", () => nullableNumber(kpis.returnRate, "再訪率")),
    questionsPerActiveUser: safe("1人あたり質問", () => nullableNumber(kpis.questionsPerActiveUser, "1人あたり質問")),
    completeDelivery: safe("回答成功率", () => measurementModel(kpis.completeDelivery)),
    p95Latency: safe("P95応答時間", () => measurementModel(kpis.p95Latency, { latency: true })),
    issues,
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

function safePart(issues, label, create) {
  try { return create(); } catch (error) {
    issues.push(`${label}: ${error?.message || "確認できません。"}`);
    return null;
  }
}

function tolerantRows(payload, key, label, parse, issues) {
  return requiredArray(payload, key, label).flatMap((row, index) => {
    try { return [parse(row)]; } catch (error) {
      issues.push(`${label} ${index + 1}行目: ${error?.message || "確認できません。"}`);
      return [];
    }
  });
}

export function environmentModel(payload) {
  const issues = [];
  return {
    hourlyQuestions: safePart(issues, "時間帯別質問", () => tolerantRows(
      payload, "hourlyQuestions", "時間帯別質問",
      (row) => ({ label: requiredText(row?.hour, "時間帯"), count: requiredCount(row?.count, "時間帯別質問") }),
      issues,
    )),
    deviceDistribution: safePart(issues, "デバイス分析", () => tolerantRows(
      payload, "deviceDistribution", "デバイス分析",
      (row) => distributionRows([row], "デバイス分析")[0],
      issues,
    )),
    deviceMeasurement: safePart(issues, "デバイス分析の計測範囲", () => coverageModel(payload.deviceMeasurement, "デバイス分析の計測範囲")),
    modeDistribution: safePart(issues, "モード分析", () => tolerantRows(
      payload, "modeDistribution", "モード分析",
      (row) => distributionRows([row], "モード分析")[0],
      issues,
    )),
    modeMeasurement: safePart(issues, "モード分析の計測範囲", () => coverageModel(payload.modeMeasurement, "モード分析の計測範囲")),
    issues,
  };
}

export function usageTrendModel(payload) {
  const issues = [];
  const rows = safePart(issues, "利用推移", () => tolerantRows(
    payload, "usageTrend", "利用推移",
    (row) => ({
      date: requiredText(row?.date, "利用推移の日付"),
      activeUsers: requiredCount(row?.activeUsers, "利用推移"),
      questions: requiredCount(row?.questions, "利用推移"),
      isPartial: requiredBoolean(row?.isPartial, "利用推移の途中集計状態"),
    }),
    issues,
  ));
  return { rows, issues };
}

export function taskModel(payload) {
  const issues = [];
  return {
    rows: safePart(issues, "質問種類", () => tolerantRows(
      payload, "requestTasks", "質問種類",
      (row) => distributionRows([row], "質問種類")[0],
      issues,
    )),
    measurement: safePart(issues, "質問種類の計測範囲", () => coverageModel(payload.taskMeasurement, "質問種類の計測範囲")),
    issues,
  };
}

export function activityModel(payload) {
  const issues = [];
  return {
    distribution: safePart(issues, "活性度分布", () => distributionRows(requiredArray(payload, "activityDistribution", "活性度分布"), "活性度分布")),
    byArea: safePart(issues, "地域別活性度", () => requiredArray(payload, "activityByArea", "地域別活性度")),
    byRole: safePart(issues, "役割別活性度", () => requiredArray(payload, "activityByRole", "役割別活性度")),
    issues,
  };
}

export function productsModel(payload) {
  const issues = [];
  return {
    topProducts: safePart(issues, "製品ランキング", () => tolerantRows(
      payload, "topProducts", "製品ランキング",
      (row) => ({ label: requiredText(row?.label, "製品名"), count: requiredCount(row?.count, "製品ランキング") }),
      issues,
    )),
    matrix: safePart(issues, "製品マトリクス", () => tolerantRows(
      payload, "productTaskMatrix", "製品マトリクス",
      (row) => ({
        product: requiredText(row?.product, "製品名"),
        task: requiredText(row?.task, "質問種類"),
        taskLabel: requiredText(row?.taskLabel, "質問種類名"),
        count: requiredCount(row?.count, "製品マトリクス"),
      }),
      issues,
    )),
    resolution: safePart(issues, "製品判定範囲", () => ({
      ...requiredObject(payload, "productResolution", "製品判定範囲"),
      ...coverageModel(payload.productResolution, "製品判定範囲"),
    })),
    issues,
  };
}

export function regionsModel(payload) {
  const scopeMetadata = scopeMetadataModel(payload, "global");
  if (!Array.isArray(payload.regions)) throw new Error("地域データの形式が不正です。");
  const metadata = analyticsMetadataModel(payload);
  metadata.metadataIssues.push(...scopeMetadata.issues);
  const contentDiagnostics = contentDiagnosticsModel(payload);
  if (contentDiagnostics.notice) metadata.metadataIssues.push(contentDiagnostics.notice);
  let scopeUserCount = null;
  try {
    scopeUserCount = requiredCount(payload.scopeUserCount, "地域対象者");
  } catch (error) {
    metadata.metadataIssues.push(error?.message || "地域対象者数を確認できません。");
  }
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
  return { scopeUserCount, scopeMetadata, contentDiagnostics, ...metadata, regions, issues };
}
