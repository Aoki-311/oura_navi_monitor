import { analyticsTaskLabel, questionCategoryLabel } from "../viewModels/labels.js";

const ACTIVITY_KEYS = new Set(["high", "middle", "low", "dormant"]);

function requiredArray(payload, key, label = "ユーザーデータ") {
  if (!Array.isArray(payload?.[key])) throw new Error(`${label}の${key}が不正です`);
  return payload[key];
}

function requiredObject(payload, key, label = "ユーザーデータ") {
  const value = payload?.[key];
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label}の${key}が不正です`);
  return value;
}

function requiredText(value, key) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`ユーザーデータの${key}が不正です`);
  return value;
}

function labelRows(value) {
  if (!Array.isArray(value)) throw new Error("ユーザーデータのlabelsが不正です");
  return value.map((row) => ({
    labelId: requiredText(row?.labelId, "labelId"),
    name: requiredText(row?.name, "label name"),
    color: requiredText(row?.color, "label color"),
  }));
}

function requiredNumber(value, key, { nullable = false } = {}) {
  if (nullable && value == null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`ユーザーデータの${key}が不正です`);
  return value;
}

export function usersModel(payload) {
  if (!payload || !["ready", "unavailable"].includes(payload.status) || typeof payload.dataThrough !== "string" || !Array.isArray(payload.users)) throw new Error("ユーザーデータの形式が不正です");
  return {
    status: payload.status,
    dataThrough: payload.dataThrough,
    users: payload.users.map((row) => {
      const activity = requiredText(row.activity, "activity");
      if (!ACTIVITY_KEYS.has(activity) || !Number.isInteger(row.activeDays7) || !Number.isInteger(row.questionCount7)) throw new Error("ユーザーデータの行形式が不正です");
      return {
        rosterId: requiredText(row.rosterId, "rosterId"),
        name: requiredText(row.name, "name"),
        email: requiredText(row.email, "email"),
        area: requiredText(row.area, "area"),
        areaKey: requiredText(row.areaKey, "areaKey"),
        labels: labelRows(row.labels),
        lastActiveAt: row.lastActiveAt == null ? "" : String(row.lastActiveAt),
        activeDays7: row.activeDays7,
        questionCount7: row.questionCount7,
        completeDeliveryRate: row.completeDeliveryRate,
        activity,
        activityLabel: requiredText(row.activityLabel, "activityLabel"),
      };
    }),
  };
}

export function userDetailModel(payload) {
  if (!payload?.profile?.rosterId || !["ready", "unavailable"].includes(payload.status) || typeof payload.dataThrough !== "string") throw new Error("ユーザー詳細の形式が不正です");
  const profile = requiredObject(payload, "profile", "ユーザー詳細");
  const summary = requiredObject(payload, "summary", "ユーザー詳細");
  const comparisons = requiredObject(payload, "comparisons", "ユーザー詳細");
  const areaComparison = requiredObject(comparisons, "area", "ユーザー詳細比較");
  const roleComparison = requiredObject(comparisons, "role", "ユーザー詳細比較");
  const comparison = (row) => ({
    label: requiredText(row.label, "comparison label"),
    peerCount: requiredNumber(row.peerCount, "peerCount"),
    averageQuestions: requiredNumber(row.averageQuestions, "averageQuestions", { nullable: true }),
    averageActiveDays: requiredNumber(row.averageActiveDays, "averageActiveDays", { nullable: true }),
    averageCompleteDeliveryRate: requiredNumber(row.averageCompleteDeliveryRate, "averageCompleteDeliveryRate", { nullable: true }),
  });
  return {
    status: payload.status,
    dataThrough: payload.dataThrough,
    profile: {
      rosterId: requiredText(profile.rosterId, "rosterId"),
      name: requiredText(profile.name, "name"),
      email: requiredText(profile.email, "email"),
      area: requiredText(profile.area, "area"),
      workplace: requiredText(profile.workplace, "workplace"),
      role: requiredText(profile.role, "role"),
      department: requiredText(profile.department, "department"),
      mrExperience: requiredText(profile.mrExperience, "mrExperience"),
      labels: labelRows(profile.labels),
    },
    summary: {
      lastActiveAt: typeof summary.lastActiveAt === "string" ? summary.lastActiveAt : (() => { throw new Error("ユーザーデータのlastActiveAtが不正です"); })(),
      activeDays: requiredNumber(summary.activeDays, "activeDays"),
      questions: requiredNumber(summary.questions, "questions"),
      questionsPerActiveDay: requiredNumber(summary.questionsPerActiveDay, "questionsPerActiveDay", { nullable: true }),
      completeDeliveryRate: requiredNumber(summary.completeDeliveryRate, "completeDeliveryRate", { nullable: true }),
    },
    comparisons: { area: comparison(areaComparison), role: comparison(roleComparison) },
    trend: requiredArray(payload, "trend", "ユーザー詳細"),
    products: requiredArray(payload, "products", "ユーザー詳細"),
    productResolution: requiredObject(payload, "productResolution", "ユーザー詳細"),
    tasks: requiredArray(payload, "tasks", "ユーザー詳細").map((row) => ({
      ...row,
      label: analyticsTaskLabel(row.key),
    })),
    questionCategories: requiredArray(payload, "questionCategories", "ユーザー詳細").map((row) => ({
      ...row,
      label: questionCategoryLabel(row.key),
    })),
    modes: requiredArray(payload, "modes", "ユーザー詳細"),
    devices: requiredArray(payload, "devices", "ユーザー詳細"),
    conversations: requiredArray(payload, "conversations", "ユーザー詳細"),
  };
}

export function messageModel(payload) {
  if (!payload || !["ready", "unavailable"].includes(payload.status) || !Array.isArray(payload.messages) || typeof payload.page?.nextCursor !== "string") throw new Error("会話メッセージの形式が不正です");
  return { status: payload.status, messages: payload.messages, nextCursor: payload.page.nextCursor };
}
