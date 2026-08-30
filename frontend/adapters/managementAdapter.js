import { isExactSummaryRoleSet } from "../contracts/analysisScopes.js";

function requiredText(value, field) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`管理データの${field}が不正です`);
  return value.trim();
}

function optionalText(value, field) {
  if (value == null) return "";
  if (typeof value !== "string") throw new Error(`管理データの${field}が不正です`);
  return value;
}

function rowsModel(payload, key, parse, sourceLabel) {
  if (!payload || !Array.isArray(payload[key])) throw new Error(`${sourceLabel}の形式が不正です`);
  const items = [];
  const issues = [];
  payload[key].forEach((row, index) => {
    try { items.push(parse(row)); }
    catch (error) { issues.push({ index, message: error.message }); }
  });
  return { items, issues };
}

function parseUser(row) {
  if (!Array.isArray(row?.labelIds) || typeof row.isActive !== "boolean" || typeof row.identityBound !== "boolean" || typeof row.globalScopeEnabled !== "boolean" || typeof row.userMapScopeEnabled !== "boolean") {
    throw new Error("管理ユーザーの行形式が不正です");
  }
  const scopePolicyVersion = optionalText(row.scopePolicyVersion, "scope policy").trim();
  const scopePolicyVerified = Boolean(scopePolicyVersion);
  const rosterIssues = Array.isArray(row.rosterIssues)
    ? row.rosterIssues.map((value) => requiredText(value, "roster issue"))
    : ["旧形式のため分析対象を再確認してください"];
  return {
    rosterId: optionalText(row.rosterId, "rosterId"),
    name: optionalText(row.name, "name") || "（氏名未設定）",
    email: optionalText(row.email, "email"),
    area: optionalText(row.area, "area"),
    areaKey: optionalText(row.areaKey, "areaKey"),
    workplace: optionalText(row.workplace, "workplace"),
    role: optionalText(row.role, "role"),
    department: optionalText(row.department, "department"),
    mrExperience: optionalText(row.mrExperience, "mrExperience") || "-",
    labelIds: row.labelIds.map((value) => requiredText(value, "labelId")),
    isActive: row.isActive,
    identityBound: row.identityBound,
    globalScopeEnabled: row.globalScopeEnabled,
    userMapScopeEnabled: row.userMapScopeEnabled,
    scopePolicyVersion: scopePolicyVersion || "legacy_unversioned",
    scopePolicyVerified,
    rosterIssues,
    updatedAt: optionalText(row.updatedAt, "updatedAt"),
    updatedBy: optionalText(row.updatedBy, "updatedBy"),
  };
}

function parseLabel(row) {
  if (!Number.isInteger(row?.usageCount) || row.usageCount < 0 || typeof row.isActive !== "boolean") {
    throw new Error("ラベルの行形式が不正です");
  }
  const labelIssues = Array.isArray(row.labelIssues)
    ? row.labelIssues.map((value) => requiredText(value, "label issue"))
    : ["旧形式のためラベル状態を確認できません"];
  return {
    labelId: optionalText(row.labelId, "labelId"),
    name: optionalText(row.name, "label name") || "（名称未設定）",
    color: optionalText(row.color, "label color") || "#5f6285",
    usageCount: row.usageCount,
    isActive: row.isActive,
    labelIssues,
    updatedAt: optionalText(row.updatedAt, "updatedAt"),
    updatedBy: optionalText(row.updatedBy, "updatedBy"),
  };
}

export function managementUsersModel(payload) {
  return rowsModel(payload, "users", parseUser, "管理ユーザー");
}

export function managementLabelsModel(payload) {
  const model = rowsModel(payload, "labels", parseLabel, "ラベル");
  model.items.forEach((item, index) => {
    if (item.labelIssues.length) {
      model.issues.push({
        index,
        message: `${item.name}: ${item.labelIssues.join(" / ")}`,
      });
    }
  });
  return model;
}

function textList(value, field) {
  if (!Array.isArray(value)) throw new Error(`管理選択肢の${field}が不正です`);
  return [...new Set(value.map((item) => requiredText(item, field)))];
}

export function managementMetadataModel(payload) {
  if (!payload || typeof payload !== "object") throw new Error("管理選択肢の形式が不正です");
  if (!isExactSummaryRoleSet(payload.summaryRoles)) {
    throw new Error("全体サマリー対象の役割契約が一致しません");
  }
  const model = {
    areas: textList(payload.areas, "areas"),
    workplaces: textList(payload.workplaces, "workplaces"),
    roles: textList(payload.roles, "roles"),
    summaryRoles: textList(payload.summaryRoles, "summaryRoles"),
    departments: textList(payload.departments, "departments"),
    scopePolicyVersion: requiredText(payload.scopePolicyVersion, "scopePolicyVersion"),
    labelColors: textList(payload.labelColors, "labelColors"),
  };
  if (!model.areas.length || !model.roles.length || !model.summaryRoles.length || !model.departments.length || !model.labelColors.length) {
    throw new Error("管理選択肢に必須項目がありません");
  }
  return model;
}

export function scopePreviewModel(payload, expectedPolicyVersion) {
  if (!payload || typeof payload.globalScopeEnabled !== "boolean" || typeof payload.userMapScopeEnabled !== "boolean") {
    throw new Error("分析対象の判定結果が不正です");
  }
  const scopePolicyVersion = requiredText(payload.scopePolicyVersion, "scopePolicyVersion");
  if (expectedPolicyVersion && scopePolicyVersion !== expectedPolicyVersion) {
    throw new Error("分析対象ポリシーが更新されました。画面を再読込してください。");
  }
  return {
    globalScopeEnabled: payload.globalScopeEnabled,
    userMapScopeEnabled: payload.userMapScopeEnabled,
    scopePolicyVersion,
  };
}
