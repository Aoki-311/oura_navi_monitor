import {
  displayCount,
  displayDateTime,
  displayNullable,
  displayRate,
  safeArray,
  truncateMiddle,
} from "../viewModels/formatters.js";
import { DEVICE_LABELS, questionCategoryLabel } from "../viewModels/labels.js";

function activityKeyFromLevel(level, rawKey = "") {
  const key = String(rawKey || "").trim().toLowerCase();
  if (key) return key;
  const label = String(level || "").trim();
  if (label.includes("高")) return "high";
  if (label.includes("中")) return "middle";
  if (label.includes("低")) return "low";
  if (label.includes("休眠")) return "dormant";
  return "";
}

export function toUserRows(payload) {
  return safeArray(payload?.users).map((row) => {
    const coverageRate = row.coverageRate ?? (row.lowCoverageRate === null || row.lowCoverageRate === undefined ? null : 1 - Number(row.lowCoverageRate || 0));
    const activityLevel = row.activityLevel || "不明";
    return {
      userId: row.userId || "",
      userEmail: row.userEmail || "",
      userIdHash: row.userIdHash || "",
      lastActiveAtJst: displayNullable(row.lastActiveAtJst),
      activeDays7: displayCount(row.activeDays7),
      messageCount7d: displayCount(row.messageCount7d),
      coverageRate: coverageRate === null || coverageRate === undefined ? "-" : displayRate(coverageRate),
      badFeedbackRate: row.badFeedbackRate === null || row.badFeedbackRate === undefined ? "-" : displayRate(row.badFeedbackRate),
      activityLevel,
      activityKey: activityKeyFromLevel(activityLevel, row.activityKey),
    };
  });
}

export function toUserDetailViewModel(payload) {
  const user = payload?.user || {};
  const summary = payload?.summary || {};
  const questionCategoryMap = new Map();
  for (const row of safeArray(payload?.questionCategoryDistribution)) {
    const label = questionCategoryLabel(row.value || row.label || "topic_ideation");
    const current = questionCategoryMap.get(label) || { label, rawLabel: row.value || row.label || "topic_ideation", count: 0 };
    current.count += Number(row.count || 0);
    questionCategoryMap.set(label, current);
  }
  const questionCategoryTotal = Array.from(questionCategoryMap.values()).reduce((sum, row) => sum + row.count, 0);
  return {
    user: {
      userId: user.userId || "",
      userEmail: user.userEmail || "",
      title: `${user.userEmail || user.userId || "ユーザー"} / ${summary.activityLevel || user.activityLevel || "不明"}`,
    },
    summaryCards: [
      { label: "メッセージ数", value: displayCount(summary.messageCount) },
      { label: "回答成功率", value: displayRate(summary.answerSuccessRate) },
      { label: "低カバレッジ率", value: displayRate(summary.lowCoverageRate) },
      { label: "低評価率", value: summary.badFeedbackRate === null || summary.badFeedbackRate === undefined ? "-" : displayRate(summary.badFeedbackRate) },
      { label: "追問数", value: displayCount(summary.followupCount) },
    ],
    trend: safeArray(payload?.trend).map((row) => ({
      label: row.date || "",
      messageCount: Number(row.messageCount || 0),
      answerSuccessRate: row.answerSuccessRate,
      lowCoverageRate: row.lowCoverageRate,
    })),
    modeDistribution: safeArray(payload?.modeDistribution).map((row) => ({
      label: row.label || row.value || "不明",
      count: Number(row.count || 0),
      rate: row.rate,
    })),
    questionCategoryDistribution: Array.from(questionCategoryMap.values())
      .map((row) => ({ ...row, rate: questionCategoryTotal ? row.count / questionCategoryTotal : null }))
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "ja")),
    deviceDistribution: safeArray(payload?.deviceDistribution).map((row) => {
      const raw = row.value || row.deviceClass || row.label || "unknown";
      return {
        label: DEVICE_LABELS[String(raw).toLowerCase()] || row.label || "不明",
        rawLabel: raw,
        count: Number(row.count || 0),
        rate: row.rate,
      };
    }),
    conversations: safeArray(payload?.conversations).map((row) => ({
      conversationId: row.conversationId || "",
      title: row.title || "-",
      titleShort: truncateMiddle(row.title || "-", 18, 8),
      mode: row.mode || "-",
      visibility: row.visibility || "-",
      createdAtJst: row.createdAtJst || displayDateTime(row.createdAt),
      updatedAtJst: row.updatedAtJst || displayDateTime(row.updatedAt),
      messageCount: displayCount(row.messageCount),
      integrityState: row.integrityState || "-",
      isFavorite: row.isFavorite ? "はい" : "いいえ",
      followupRuntimeSummary: JSON.stringify(row.followupRuntimeSummary || {}),
    })),
    nextCursor: payload?.page?.nextCursor || "",
  };
}

export function toMessageRows(payload) {
  return safeArray(payload?.messages).map((row) => {
    const rawRole = String(row.roleRaw || row.role || "").toLowerCase();
    const isUser = rawRole === "user" || row.role === "ユーザー" || row.roleLabel === "ユーザー";
    const rawCategory = row.questionCategory || row.intentFamily || "topic_ideation";
    return {
      timestamp: row.timestampJst || displayDateTime(row.timestamp),
      role: row.roleLabel || row.role || "-",
      status: row.statusLabel || row.status || "-",
      mode: row.modeAtSendLabel || row.modeAtSend || "-",
      device: row.deviceLabel || row.deviceClass || "-",
      feedback: row.feedback || "none",
      questionCategory: isUser ? questionCategoryLabel(rawCategory) : "",
      contentPreview: row.content || row.contentPreview || "-",
      coverageRate:
        row.coverageRate === null || row.coverageRate === undefined
          ? row.lowCoverageRate === null || row.lowCoverageRate === undefined
            ? "-"
            : displayRate(1 - Number(row.lowCoverageRate || 0))
          : displayRate(row.coverageRate),
      traceId: row.traceId || "",
      requestId: row.requestId || "",
      turnId: row.turnId || "",
      messageId: row.messageId || "",
      traceShort: truncateMiddle(row.traceId),
      requestShort: truncateMiddle(row.requestId),
      turnShort: truncateMiddle(row.turnId),
      messageShort: truncateMiddle(row.messageId),
    };
  });
}
