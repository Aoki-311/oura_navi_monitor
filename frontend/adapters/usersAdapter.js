import { analyticsMetadataModel, coverageModel, measurementModel, scopeMetadataModel } from "./overviewAdapter.js";
import { isSummaryRole } from "../contracts/analysisScopes.js";
import { contentDiagnosticsModel as parseContentDiagnostics } from "./contentDiagnosticsAdapter.js";

const ACTIVITY_KEYS = new Set(["high", "middle", "low", "dormant"]);

function requiredText(value, key) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${key}を確認できません。`);
  return value;
}

function optionalText(value) {
  return typeof value === "string" ? value : "";
}

function requiredObject(payload, key, label) {
  const value = payload?.[key];
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label}を表示できません。`);
  return value;
}

function requiredArray(payload, key, label) {
  if (!Array.isArray(payload?.[key])) throw new Error(`${label}を表示できません。`);
  return payload[key];
}

function requiredNumber(value, key, { nullable = false, integer = false } = {}) {
  if (nullable && value == null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || (integer && !Number.isInteger(value))) throw new Error(`${key}を確認できません。`);
  return value;
}

function requiredBoolean(value, key) {
  if (typeof value !== "boolean") throw new Error(`${key}を確認できません。`);
  return value;
}

function labels(value) {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row) => {
    try {
      return [{ labelId: requiredText(row?.labelId, "ラベルID"), name: requiredText(row?.name, "ラベル名"), color: requiredText(row?.color, "ラベル色") }];
    } catch (_error) {
      return [];
    }
  });
}

export function contentDiagnosticsModel(payload) {
  return parseContentDiagnostics(payload);
}

export function usersModel(payload, expectedScope = "user_map") {
  if (!payload || !Array.isArray(payload.users)) throw new Error("ユーザーデータの形式が不正です。");
  const scopeMetadata = scopeMetadataModel(payload, expectedScope);
  const metadata = analyticsMetadataModel(payload);
  metadata.metadataIssues.push(...scopeMetadata.issues);
  let contentDiagnostics = contentDiagnosticsModel(payload);
  let scopeUserCount = Number.isInteger(payload.scopeUserCount) && payload.scopeUserCount >= 0
    ? payload.scopeUserCount
    : null;
  if (scopeUserCount == null) metadata.metadataIssues.push("ユーザー対象者数を確認できません。");
  const issues = [];
  let isolatedSummaryRoleCount = 0;
  const users = payload.users.flatMap((row, index) => {
    if (expectedScope === "global" && !isSummaryRole(row?.role)) {
      isolatedSummaryRoleCount += 1;
      const role = typeof row?.role === "string" && row.role.trim() ? row.role : "未取得";
      issues.push(`${index + 1}行目を表示できません: 全体サマリー対象外の役割「${role}」を検出しました。`);
      return [];
    }
    try {
      const rowIssues = [];
      const activity = ACTIVITY_KEYS.has(row?.activity) ? row.activity : null;
      if (!activity) rowIssues.push("活性度は未計測です。");
      const activeDays7 = Number.isInteger(row?.activeDays7) && row.activeDays7 >= 0 ? row.activeDays7 : null;
      const userMessageCount7 = Number.isInteger(row?.userMessageCount7) && row.userMessageCount7 >= 0 ? row.userMessageCount7 : null;
      if (activeDays7 == null || userMessageCount7 == null) rowIssues.push("直近7日の利用値は未計測です。");
      const workplace = optionalText(row?.workplace);
      const role = optionalText(row?.role);
      const department = optionalText(row?.department);
      if (!workplace || !role || !department) rowIssues.push("旧形式のため役割・部門・勤務地の一部を確認できません。");
      let completeDelivery = null;
      try { completeDelivery = measurementModel(row?.completeDelivery); } catch (error) {
        rowIssues.push(`回答成功率: ${error?.message || "確認できません。"}`);
      }
      const item = {
        rosterId: requiredText(row?.rosterId, "ユーザーID"),
        name: requiredText(row?.name, "社員名"),
        email: requiredText(row?.email, "メール"),
        area: requiredText(row?.area, "エリア"),
        areaKey: requiredText(row?.areaKey, "エリアキー"),
        workplace: workplace || "未取得",
        role: role || "未取得",
        department: department || "未取得",
        labels: labels(row?.labels),
        lastActiveAt: optionalText(row?.lastActiveAt),
        activeDays7,
        userMessageCount7,
        activeDaysInPeriod: requiredNumber(row?.activeDaysInPeriod, "期間内利用日数", { integer: true }),
        userMessageCountInPeriod: requiredNumber(row?.userMessageCountInPeriod, "期間内質問数", { integer: true }),
        completeDelivery,
        activity,
        activityLabel: activity ? requiredText(row?.activityLabel, "活性度ラベル") : "未測定",
        issues: rowIssues,
      };
      issues.push(...rowIssues.map((message) => `${item.name}: ${message}`));
      return [item];
    } catch (error) {
      issues.push(`${index + 1}行目を表示できません: ${error.message}`);
      return [];
    }
  });
  if (isolatedSummaryRoleCount > 0) {
    const roleNotice = `全体サマリー対象外の役割を含む ${isolatedSummaryRoleCount}件のユーザー行を除外しました。残りの利用状況は表示しています。`;
    const priorRosterIsolatedCount = Number.isInteger(contentDiagnostics.rosterIsolatedCount)
      ? contentDiagnostics.rosterIsolatedCount
      : 0;
    contentDiagnostics = {
      ...contentDiagnostics,
      state: "degraded",
      issues: [...new Set([...contentDiagnostics.issues, "unexpected_summary_role"])],
      notice: [contentDiagnostics.notice, roleNotice].filter(Boolean).join(" "),
      exportAvailable: false,
      rosterStatus: "partial",
      rosterIsolatedCount: contentDiagnostics.rosterDiagnosticsAvailable
        ? priorRosterIsolatedCount + isolatedSummaryRoleCount
        : null,
      rosterIssueCounts: {
        ...contentDiagnostics.rosterIssueCounts,
        unexpected_summary_role: isolatedSummaryRoleCount,
      },
      isolatedSummaryRoleCount,
    };
    scopeUserCount = null;
    metadata.metadataIssues.push("全体サマリー対象人数を確認できません。");
  }
  if (contentDiagnostics.notice) metadata.metadataIssues.push(contentDiagnostics.notice);
  return { scopeUserCount, scopeMetadata, contentDiagnostics, ...metadata, users, issues };
}

export function userDetailEnvelope(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("ユーザー分析データの形式が不正です。");
  const scopeMetadata = scopeMetadataModel(payload, "user_map");
  const metadata = analyticsMetadataModel(payload, { includeQuality: true });
  metadata.metadataIssues.push(...scopeMetadata.issues);
  const contentDiagnostics = contentDiagnosticsModel(payload);
  if (contentDiagnostics.notice) metadata.metadataIssues.push(contentDiagnostics.notice);
  return {
    ...payload,
    scopeMetadata,
    contentDiagnostics,
    ...metadata,
  };
}

export function userProfileModel(payload) {
  const profile = requiredObject(payload, "profile", "個人プロフィール");
  const issues = [];
  const legacyText = (value, label) => {
    try { return requiredText(value, label); } catch (error) {
      issues.push(`${label}: ${error?.message || "確認できません。"}`);
      return "未取得";
    }
  };
  return {
    rosterId: requiredText(profile.rosterId, "ユーザーID"),
    name: requiredText(profile.name, "社員名"),
    email: requiredText(profile.email, "メール"),
    area: legacyText(profile.area, "エリア"),
    workplace: legacyText(profile.workplace, "勤務地"),
    role: legacyText(profile.role, "役割"),
    department: legacyText(profile.department, "部門"),
    mrExperience: legacyText(profile.mrExperience, "MR経験"),
    labels: labels(profile.labels),
    issues,
  };
}

export function userSummaryModel(payload) {
  const summary = requiredObject(payload, "summary", "個人利用サマリー");
  const issues = [];
  const safe = (label, create) => {
    try { return create(); } catch (error) {
      issues.push(`${label}: ${error?.message || "確認できません。"}`);
      return null;
    }
  };
  return {
    lastActiveAt: optionalText(summary.lastActiveAt),
    activeDays: safe("利用日数", () => requiredNumber(summary.activeDays, "利用日数", { integer: true })),
    questions: safe("質問数", () => requiredNumber(summary.questions, "質問数", { integer: true })),
    questionsPerActiveDay: safe("1日平均質問", () => requiredNumber(summary.questionsPerActiveDay, "1日平均質問", { nullable: true })),
    completeDelivery: safe("回答成功率", () => measurementModel(summary.completeDelivery)),
    p95Latency: safe("P95応答時間", () => measurementModel(summary.p95Latency, { latency: true })),
    issues,
  };
}

function comparison(row) {
  return {
    label: requiredText(row?.label, "比較対象"),
    peerCount: requiredNumber(row?.peerCount, "比較人数", { integer: true }),
    averageQuestions: requiredNumber(row?.averageQuestions, "平均質問数", { nullable: true }),
    averageActiveDays: requiredNumber(row?.averageActiveDays, "平均利用日", { nullable: true }),
    averageCompleteDelivery: measurementModel(row?.averageCompleteDelivery),
  };
}

export function userComparisonsModel(payload) {
  const issues = [];
  let comparisons = null;
  try { comparisons = requiredObject(payload, "comparisons", "比較分析"); } catch (error) {
    issues.push(error?.message || "比較分析を確認できません。");
  }
  const safe = (label, value) => {
    try { return comparison(value); } catch (error) {
      issues.push(`${label}: ${error?.message || "確認できません。"}`);
      return null;
    }
  };
  return {
    area: comparisons ? safe("地域比較", comparisons.area) : null,
    role: comparisons ? safe("役割比較", comparisons.role) : null,
    issues,
  };
}

export function userTrendModel(payload) {
  const issues = [];
  const rows = requiredArray(payload, "trend", "個人利用推移").flatMap((row, index) => {
    try {
      return [{
        date: requiredText(row?.date, "日付"),
        questions: requiredNumber(row?.questions, "質問数", { integer: true }),
        completeDelivery: measurementModel(row?.completeDelivery),
        isPartial: requiredBoolean(row?.isPartial, "個人利用推移の途中集計状態"),
      }];
    } catch (error) {
      issues.push(`${index + 1}行目: ${error?.message || "確認できません。"}`);
      return [];
    }
  });
  return { rows, issues };
}

function distributionRows(rows) {
  return rows.map((row) => ({
    key: optionalText(row?.key),
    label: requiredText(row?.label, "分析ラベル"),
    count: requiredNumber(row?.count, "件数", { integer: true }),
    rate: requiredNumber(row?.rate, "割合", { nullable: true }),
  }));
}

function tolerantDistribution(payload, key, label, issues) {
  return requiredArray(payload, key, label).flatMap((row, index) => {
    try { return distributionRows([row]); } catch (error) {
      issues.push(`${label} ${index + 1}行目: ${error?.message || "確認できません。"}`);
      return [];
    }
  });
}

export function userNeedsModel(payload) {
  const issues = [];
  const safe = (label, create) => {
    try { return create(); } catch (error) {
      issues.push(`${label}: ${error?.message || "確認できません。"}`);
      return null;
    }
  };
  const productResolution = safe("製品判定範囲", () => ({
    ...requiredObject(payload, "productResolution", "製品判定範囲"),
    ...coverageModel(payload.productResolution, "製品判定範囲"),
  }));
  return {
    products: safe("製品分析", () => requiredArray(payload, "products", "製品分析").flatMap((row, index) => {
      try {
        return [{
          label: requiredText(row?.label, "製品名"),
          count: requiredNumber(row?.count, "製品質問数", { integer: true }),
        }];
      } catch (error) {
        issues.push(`製品分析 ${index + 1}行目: ${error?.message || "確認できません。"}`);
        return [];
      }
    })),
    productResolution,
    tasks: safe("質問種類", () => tolerantDistribution(payload, "tasks", "質問種類", issues)),
    taskMeasurement: safe("質問種類の計測範囲", () => coverageModel(payload.taskMeasurement, "質問種類の計測範囲")),
    questionCategories: safe("質問テーマ", () => tolerantDistribution(payload, "questionCategories", "質問テーマ", issues)),
    questionCategoryMeasurement: safe("質問テーマの計測範囲", () => coverageModel(payload.questionCategoryMeasurement, "質問テーマの計測範囲")),
    modes: safe("モード分析", () => tolerantDistribution(payload, "modes", "モード分析", issues)),
    modeMeasurement: safe("モード分析の計測範囲", () => coverageModel(payload.modeMeasurement, "モード分析の計測範囲")),
    devices: safe("デバイス分析", () => tolerantDistribution(payload, "devices", "デバイス分析", issues)),
    deviceMeasurement: safe("デバイス分析の計測範囲", () => coverageModel(payload.deviceMeasurement, "デバイス分析の計測範囲")),
    issues,
  };
}

export function conversationsModel(payload) {
  if (!payload || !["ready", "identity_unmatched"].includes(payload.status) || !Array.isArray(payload.conversations)) throw new Error("会話一覧の形式が不正です。");
  const issues = [];
  const conversations = payload.conversations.flatMap((row, index) => {
    try {
      return [{
        conversationId: requiredText(row?.conversationId, "会話ID"),
        title: optionalText(row?.title),
        messageCount: requiredNumber(row?.messageCount, "メッセージ数", { integer: true }),
        updatedAt: optionalText(row?.updatedAt),
        updatedAtJst: optionalText(row?.updatedAtJst),
      }];
    } catch (error) {
      issues.push(`${index + 1}行目: ${error.message}`);
      return [];
    }
  });
  return { status: payload.status, conversations, issues };
}

export function messageModel(payload) {
  if (!payload || !["ready", "identity_unmatched"].includes(payload.status) || !Array.isArray(payload.messages) || typeof payload.page?.nextCursor !== "string") throw new Error("会話メッセージの形式が不正です。");
  const issues = [];
  const messages = payload.messages.flatMap((row, index) => {
    try {
      return [{
        messageId: requiredText(row?.messageId, "メッセージID"),
        timestampJst: optionalText(row?.timestampJst),
        role: requiredText(row?.role, "発言者"),
        roleLabel: optionalText(row?.roleLabel),
        content: optionalText(row?.content),
      }];
    } catch (error) {
      issues.push(`${index + 1}行目: ${error.message}`);
      return [];
    }
  });
  return { status: payload.status, messages, nextCursor: payload.page.nextCursor, issues };
}
