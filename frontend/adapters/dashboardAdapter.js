import { displayCount, displayMs, displayRate, safeArray } from "../viewModels/formatters.js";
import { ACTIVITY_DEFINITIONS, KPI_HELP, PRESET_LABELS, QUALITY_LABELS } from "../viewModels/labels.js";
import { toMetricStatusBadge } from "../viewModels/metricStatus.js";

function qualityRows(rows) {
  return safeArray(rows).map((row) => ({
    label: QUALITY_LABELS[String(row.label || "").toLowerCase()] || row.label || "不明",
    rawLabel: row.label || "unknown",
    count: Number(row.count || 0),
    rate: row.rate,
  }));
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
  const activity = payload?.activityDistribution || {};
  const environment = payload?.environmentMode || {};
  const answerQuality = payload?.answerQuality || {};
  const followup = payload?.followup || {};
  const generatedAt = payload?.meta?.generatedAt || "";

  return {
    windowLabel: PRESET_LABELS[preset] || PRESET_LABELS.today,
    generatedAt,
    fetchMs: payload?.meta?.fetchMs,
    kpis: [
      buildKpi(
        "activeUserCount",
        "アクティブユーザー数",
        displayCount(kpis.activeUserCount),
        KPI_HELP.activeUserCount,
      ),
      buildKpi(
        "answerSuccessRate",
        "回答成功率",
        displayRate(kpis.answerSuccessRate),
        KPI_HELP.answerSuccessRate,
        answerStatus,
        answerStatus.tone,
      ),
      buildKpi(
        "lowCoverageRate",
        "低カバレッジ率",
        displayRate(kpis.lowCoverageRate),
        KPI_HELP.lowCoverageRate,
        null,
        Number(kpis.lowCoverageRate || 0) >= 0.25 ? "warning" : "success",
      ),
      buildKpi(
        "errorRate",
        "エラー率",
        displayRate(kpis.errorRate),
        KPI_HELP.errorRate,
        null,
        Number(kpis.errorRate || 0) > 0.03 ? "danger" : "success",
      ),
      buildKpi("p95LatencyMs", "P95応答時間", displayMs(kpis.p95LatencyMs), KPI_HELP.p95LatencyMs),
    ],
    usageTrend: safeArray(payload?.usageTrend).map((row) => ({
      label: row.date || "",
      activeUserCount: Number(row.activeUserCount || 0),
      messageCount: Number(row.messageCount || 0),
    })),
    activityDistribution: {
      totalUserCount: Number(activity.totalUserCount || 0),
      segments: safeArray(activity.segments).map((row) => {
        const activityKey = activityKeyFromLabel(row);
        return {
          label: row.label || "不明",
          count: Number(row.count || 0),
          rate: row.rate,
          definition: ACTIVITY_DEFINITIONS[activityKey] || "",
        };
      }),
    },
    environmentMode: {
      requestByHour: safeArray(environment.requestByHour).map((row) => ({
        label: row.hour || "",
        count: Number(row.requestCount || 0),
      })),
      deviceDistribution: safeArray(environment.deviceDistribution).map((row) => ({
        label: row.label || row.value || "不明",
        count: Number(row.count || 0),
        rate: row.rate,
      })),
      modeDistribution: safeArray(environment.modeDistribution).map((row) => ({
        label: row.label || row.value || "不明",
        count: Number(row.count || 0),
        rate: row.rate,
      })),
    },
    answerQuality: [
      { key: "answerability", title: "回答可能性", rows: qualityRows(answerQuality.answerability) },
      { key: "usability", title: "回答利用可能性", rows: qualityRows(answerQuality.usability) },
      { key: "deliveryReadiness", title: "業務利用可能性", rows: qualityRows(answerQuality.deliveryReadiness) },
      { key: "evidenceSufficiency", title: "根拠十分性", rows: qualityRows(answerQuality.evidenceSufficiency) },
    ],
    followup: {
      cards: [
        { label: "追問認識数", value: displayCount(followup.recognizedCount) },
        { label: "追問成功率", value: displayRate(followup.successRate) },
        { label: "明示的な訂正", value: displayCount(followup.explicitCorrectionCount) },
        { label: "確認が必要な追問", value: displayCount(followup.clarificationRequiredCount) },
      ],
      funnel: [
        { label: "追問認識", count: Number(followup.recognizedCount || 0) },
        { label: "追問成功", count: Number(followup.successCount || 0) },
        { label: "明示的な訂正", count: Number(followup.explicitCorrectionCount || 0) },
        { label: "確認が必要な追問", count: Number(followup.clarificationRequiredCount || 0) },
      ],
    },
  };
}
