import { displayCount, displayMs, displayRate, safeArray } from "../viewModels/formatters.js";
import { ACTIVITY_DEFINITIONS, KPI_HELP, PRESET_LABELS, QUALITY_LABELS, questionCategoryLabel } from "../viewModels/labels.js";
import { toMetricStatusBadge } from "../viewModels/metricStatus.js";

function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== null);
}

function numberValue(...values) {
  return Number(firstDefined(...values) || 0);
}

function defaultRequestByHourRows() {
  return Array.from({ length: 24 }, (_, hour) => ({
    hour: `${String(hour).padStart(2, "0")}:00`,
    requestCount: 0,
  }));
}

function qualityRows(rows) {
  return safeArray(rows).map((row) => ({
    label: QUALITY_LABELS[String(row.label || "").toLowerCase()] || row.label || "不明",
    rawLabel: row.label || "unknown",
    count: Number(row.count || 0),
    rate: row.rate,
  }));
}

function distributionRows(rows, labelFn = (value) => value || "不明") {
  const normalizedRows = safeArray(rows);
  const total = normalizedRows.reduce((sum, row) => sum + numberValue(row.count, row.value, row.requestCount), 0);
  return normalizedRows.map((row) => {
    const rawValue = row.value || row.label || "unknown";
    const count = numberValue(row.count, row.requestCount, row.valueCount);
    return {
      label: labelFn(rawValue),
      rawLabel: rawValue,
      count,
      rate: firstDefined(row.rate, row.ratio, total ? count / total : null),
    };
  });
}

function activityKeyFromLabel(row) {
  const rawKey = String(row.activityKey || row.key || "").trim().toLowerCase();
  if (rawKey) return rawKey;
  const label = String(row.label || "").trim();
  if (label.includes("高")) return "high";
  if (label.includes("中")) return "middle";
  if (label.includes("低")) return "low";
  if (label.includes("休眠")) return "dormant";
  return "";
}

function buildKpi(key, label, value, help, statusBadge = null, tone = "neutral") {
  return { key, label, value, help, statusBadge, tone };
}

export function toDashboardViewModel(payload, preset = "today") {
  const kpis = payload?.kpis || {};
  const metricStatus = payload?.meta?.metricStatus || {};
  const answerStatus = toMetricStatusBadge(metricStatus.answerSuccessRate);
  const activity = payload?.activityDistribution || payload?.activity || {};
  const environment = payload?.environmentMode || payload?.environment || payload?.modeDevice || {};
  const answerQuality = payload?.answerQuality || payload?.distributions || {};
  const questionCategory = payload?.questionCategory || payload?.questionCategoryDistribution || payload?.intentFamilyDistribution || {};
  const followup = payload?.followup || payload?.followupSummary || {};
  const generatedAt = payload?.meta?.generatedAt || "";
  const usageTrendRows = safeArray(firstDefined(payload?.usageTrend, payload?.usage?.trend, payload?.trends?.usage, payload?.requestTrend));
  const activityRows = safeArray(firstDefined(activity.segments, activity.items, activity.distribution));
  const normalizedActivityRows = activityRows.length
    ? activityRows
    : [
        { label: "高アクティブ", count: 0 },
        { label: "中アクティブ", count: 0 },
        { label: "低アクティブ", count: 0 },
        { label: "休眠ユーザー", count: 0 },
      ];
  const activityTotal = numberValue(
    activity.totalUserCount,
    activity.total_user_count,
    activity.totalCount,
    kpis.totalUserCount,
    normalizedActivityRows.reduce((sum, row) => sum + numberValue(row.count, row.userCount, row.value), 0),
  );
  const requestByHourRows = safeArray(firstDefined(environment.requestByHour, environment.requestsByHour, environment.hourlyRequests));
  const normalizedRequestByHourRows = requestByHourRows.length ? requestByHourRows : defaultRequestByHourRows();
  const deviceRows = safeArray(firstDefined(environment.deviceDistribution, payload?.deviceDistribution, environment.devices));
  const normalizedDeviceRows = deviceRows.length ? deviceRows : [{ label: "PC", count: 0 }, { label: "モバイル", count: 0 }, { label: "不明", count: 0 }];
  const modeRows = safeArray(firstDefined(environment.modeDistribution, payload?.modeDistribution, environment.modes));
  const normalizedModeRows = modeRows.length ? modeRows : [{ label: "社内モード", count: 0 }, { label: "Web検索モード", count: 0 }];
  const deviceTotal = normalizedDeviceRows.reduce((sum, row) => sum + numberValue(row.count, row.requestCount, row.request_count), 0);
  const modeTotal = normalizedModeRows.reduce((sum, row) => sum + numberValue(row.count, row.requestCount, row.request_count), 0);
  const recognizedCount = numberValue(followup.recognizedCount, followup.followupRecognizedCount, followup.recognized_count);
  const successCount = numberValue(followup.successCount, followup.followupSuccessCount, followup.success_count);
  const explicitCorrectionCount = numberValue(followup.explicitCorrectionCount, followup.explicit_correction_count);
  const clarificationRequiredCount = numberValue(followup.clarificationRequiredCount, followup.clarification_required_count);

  return {
    windowLabel: PRESET_LABELS[preset] || PRESET_LABELS.today,
    generatedAt,
    fetchMs: payload?.meta?.fetchMs,
    kpis: [
      buildKpi(
        "activeUserCount",
        "アクティブユーザー数",
        displayCount(firstDefined(kpis.activeUserCount, kpis.active_user_count, kpis.activeUsers)),
        KPI_HELP.activeUserCount,
      ),
      buildKpi(
        "answerSuccessRate",
        "回答成功率",
        displayRate(firstDefined(kpis.answerSuccessRate, kpis.answer_success_rate)),
        KPI_HELP.answerSuccessRate,
        answerStatus,
        answerStatus.tone,
      ),
      buildKpi(
        "lowCoverageRate",
        "低カバレッジ率",
        displayRate(firstDefined(kpis.lowCoverageRate, kpis.low_coverage_rate)),
        KPI_HELP.lowCoverageRate,
        null,
        Number(firstDefined(kpis.lowCoverageRate, kpis.low_coverage_rate, 0)) >= 0.25 ? "warning" : "success",
      ),
      buildKpi(
        "errorRate",
        "エラー率",
        displayRate(firstDefined(kpis.errorRate, kpis.error_rate)),
        KPI_HELP.errorRate,
        null,
        Number(firstDefined(kpis.errorRate, kpis.error_rate, 0)) > 0.03 ? "danger" : "success",
      ),
      buildKpi("p95LatencyMs", "P95応答時間", displayMs(firstDefined(kpis.p95LatencyMs, kpis.p95_latency_ms)), KPI_HELP.p95LatencyMs),
    ],
    usageTrend: usageTrendRows.map((row) => ({
      label: row.date || row.label || row.bucket || row.hour || "",
      activeUserCount: numberValue(row.activeUserCount, row.active_user_count, row.userCount, row.users),
      messageCount: numberValue(row.messageCount, row.message_count, row.coreRequestCount, row.requestCount, row.count),
    })),
    activityDistribution: {
      totalUserCount: activityTotal,
      segments: normalizedActivityRows.map((row) => {
        const activityKey = activityKeyFromLabel(row);
        const count = numberValue(row.count, row.userCount, row.value);
        return {
          label: row.label || "不明",
          count,
          rate: firstDefined(row.rate, row.ratio, activityTotal ? count / activityTotal : null),
          definition: ACTIVITY_DEFINITIONS[activityKey] || "",
        };
      }),
    },
    environmentMode: {
      requestByHour: normalizedRequestByHourRows.map((row) => ({
        label: row.hour || row.label || row.bucket || "",
        count: numberValue(row.requestCount, row.request_count, row.count),
      })),
      deviceDistribution: normalizedDeviceRows.map((row) => {
        const count = numberValue(row.count, row.requestCount, row.request_count);
        return {
          label: row.label || row.value || "不明",
          count,
          rate: firstDefined(row.rate, row.ratio, deviceTotal ? count / deviceTotal : null),
        };
      }),
      modeDistribution: normalizedModeRows.map((row) => {
        const count = numberValue(row.count, row.requestCount, row.request_count);
        return {
          label: row.label || row.value || "不明",
          count,
          rate: firstDefined(row.rate, row.ratio, modeTotal ? count / modeTotal : null),
        };
      }),
    },
    answerQuality: [
      { key: "usability", title: "回答利用可能性", rows: qualityRows(answerQuality.usability) },
      { key: "evidenceSufficiency", title: "根拠十分性", rows: qualityRows(answerQuality.evidenceSufficiency) },
    ],
    questionCategory: distributionRows(
      firstDefined(questionCategory.items, questionCategory.segments, questionCategory),
      questionCategoryLabel,
    ),
    followup: {
      cards: [
        { label: "追問認識数", value: displayCount(recognizedCount) },
        { label: "追問成功率", value: displayRate(firstDefined(followup.successRate, recognizedCount ? successCount / recognizedCount : null)) },
        { label: "明示的な訂正", value: displayCount(explicitCorrectionCount) },
        { label: "確認が必要な追問", value: displayCount(clarificationRequiredCount) },
      ],
      funnel: [
        { label: "追問認識", count: recognizedCount },
        { label: "追問成功", count: successCount },
        { label: "明示的な訂正", count: explicitCorrectionCount },
        { label: "確認が必要な追問", count: clarificationRequiredCount },
      ],
    },
  };
}
