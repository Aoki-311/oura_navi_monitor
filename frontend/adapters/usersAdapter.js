import { coverageModel, measurementModel } from "./overviewAdapter.js";

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
  if (typeof value !== "number" || !Number.isFinite(value) || (integer && !Number.isInteger(value))) throw new Error(`${key}を確認できません。`);
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

export function usersModel(payload) {
  if (!payload || !Array.isArray(payload.users) || !Number.isInteger(payload.scopeUserCount) || payload.scopeUserCount < 0) throw new Error("ユーザーデータの形式が不正です。");
  const issues = [];
  const users = payload.users.flatMap((row, index) => {
    try {
      const rowIssues = [];
      const activity = ACTIVITY_KEYS.has(row?.activity) ? row.activity : null;
      if (!activity) rowIssues.push("活性度は未計測です。");
      const activeDays7 = Number.isInteger(row?.activeDays7) && row.activeDays7 >= 0 ? row.activeDays7 : null;
      const userMessageCount7 = Number.isInteger(row?.userMessageCount7) && row.userMessageCount7 >= 0 ? row.userMessageCount7 : null;
      if (activeDays7 == null || userMessageCount7 == null) rowIssues.push("直近7日の利用値は未計測です。");
      const item = {
        rosterId: requiredText(row?.rosterId, "ユーザーID"),
        name: requiredText(row?.name, "社員名"),
        email: requiredText(row?.email, "メール"),
        area: requiredText(row?.area, "エリア"),
        areaKey: requiredText(row?.areaKey, "エリアキー"),
        labels: labels(row?.labels),
        lastActiveAt: optionalText(row?.lastActiveAt),
        activeDays7,
        userMessageCount7,
        completeDelivery: measurementModel(row?.completeDelivery),
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
  return { scopeUserCount: payload.scopeUserCount, freshness: payload.freshness, users, issues };
}

export function userDetailEnvelope(payload) {
  if (!payload || !payload.freshness || !["fresh", "stale", "unknown"].includes(payload.freshness.state)) throw new Error("ユーザー分析データの形式が不正です。");
  return payload;
}

export function userProfileModel(payload) {
  const profile = requiredObject(payload, "profile", "個人プロフィール");
  return {
    rosterId: requiredText(profile.rosterId, "ユーザーID"),
    name: requiredText(profile.name, "社員名"),
    email: requiredText(profile.email, "メール"),
    area: requiredText(profile.area, "エリア"),
    workplace: requiredText(profile.workplace, "勤務地"),
    role: requiredText(profile.role, "役割"),
    department: requiredText(profile.department, "部門"),
    mrExperience: requiredText(profile.mrExperience, "MR経験"),
    labels: labels(profile.labels),
  };
}

export function userSummaryModel(payload) {
  const summary = requiredObject(payload, "summary", "個人利用サマリー");
  return {
    lastActiveAt: optionalText(summary.lastActiveAt),
    activeDays: requiredNumber(summary.activeDays, "利用日数", { integer: true }),
    questions: requiredNumber(summary.questions, "質問数", { integer: true }),
    questionsPerActiveDay: requiredNumber(summary.questionsPerActiveDay, "1日平均質問", { nullable: true }),
    completeDelivery: measurementModel(summary.completeDelivery),
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
  const comparisons = requiredObject(payload, "comparisons", "比較分析");
  return { area: comparison(comparisons.area), role: comparison(comparisons.role) };
}

export function userTrendModel(payload) {
  return requiredArray(payload, "trend", "個人利用推移").map((row) => ({
    date: requiredText(row?.date, "日付"),
    questions: requiredNumber(row?.questions, "質問数", { integer: true }),
    completeDelivery: measurementModel(row?.completeDelivery),
  }));
}

function distributionRows(rows) {
  return rows.map((row) => ({
    key: optionalText(row?.key),
    label: requiredText(row?.label, "分析ラベル"),
    count: requiredNumber(row?.count, "件数", { integer: true }),
    rate: requiredNumber(row?.rate, "割合", { nullable: true }),
  }));
}

export function userNeedsModel(payload) {
  return {
    products: requiredArray(payload, "products", "製品分析").map((row) => ({
      label: requiredText(row?.label, "製品名"),
      count: requiredNumber(row?.count, "製品質問数", { integer: true }),
    })),
    productResolution: {
      ...requiredObject(payload, "productResolution", "製品判定範囲"),
      ...coverageModel(payload.productResolution, "製品判定範囲"),
    },
    tasks: distributionRows(requiredArray(payload, "tasks", "質問種類")),
    taskMeasurement: coverageModel(payload.taskMeasurement, "質問種類の計測範囲"),
    questionCategories: distributionRows(requiredArray(payload, "questionCategories", "質問テーマ")),
    questionCategoryMeasurement: coverageModel(payload.questionCategoryMeasurement, "質問テーマの計測範囲"),
    modes: distributionRows(requiredArray(payload, "modes", "モード分析")),
    modeMeasurement: coverageModel(payload.modeMeasurement, "モード分析の計測範囲"),
    devices: distributionRows(requiredArray(payload, "devices", "デバイス分析")),
    deviceMeasurement: coverageModel(payload.deviceMeasurement, "デバイス分析の計測範囲"),
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
