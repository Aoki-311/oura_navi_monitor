import { questionCategoryLabel } from "../viewModels/labels.js";

function requiredArray(payload, key) {
  if (!Array.isArray(payload?.[key])) throw new Error(`全体サマリーの${key}が不正です`);
  return payload[key];
}

function requiredObject(payload, key) {
  const value = payload?.[key];
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`全体サマリーの${key}が不正です`);
  return value;
}

export function overviewModel(payload) {
  if (!payload || payload.scope !== "global" || !["ready", "unavailable"].includes(payload.status) || typeof payload.dataThrough !== "string") throw new Error("全体サマリーのデータ形式が不正です");
  return {
    status: payload.status,
    dataThrough: payload.dataThrough,
    kpis: requiredObject(payload, "kpis"),
    hourlyQuestions: requiredArray(payload, "hourlyQuestions"),
    deviceDistribution: requiredArray(payload, "deviceDistribution"),
    modeDistribution: requiredArray(payload, "modeDistribution"),
    usageTrend: requiredArray(payload, "usageTrend"),
    questionCategories: requiredArray(payload, "questionCategories").map((row) => ({
      ...row,
      label: questionCategoryLabel(row.key),
    })),
    activityDistribution: requiredArray(payload, "activityDistribution"),
    activityByArea: requiredArray(payload, "activityByArea"),
    activityByRole: requiredArray(payload, "activityByRole"),
    topProducts: requiredArray(payload, "topProducts"),
    productQuestionMatrix: requiredArray(payload, "productQuestionMatrix").map((row) => ({
      ...row,
      categoryLabel: questionCategoryLabel(row.category),
    })),
    productResolution: requiredObject(payload, "productResolution"),
  };
}

export function regionsModel(payload) {
  if (!payload || !["ready", "unavailable"].includes(payload.status) || typeof payload.dataThrough !== "string" || !Array.isArray(payload.regions)) throw new Error("地域データの形式が不正です");
  return { status: payload.status, dataThrough: payload.dataThrough, regions: payload.regions };
}
