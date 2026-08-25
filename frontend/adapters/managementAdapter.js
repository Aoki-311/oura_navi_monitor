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
  if (!Array.isArray(row?.labelIds) || typeof row.isActive !== "boolean" || typeof row.identityBound !== "boolean") {
    throw new Error("管理ユーザーの行形式が不正です");
  }
  return {
    rosterId: requiredText(row.rosterId, "rosterId"),
    name: requiredText(row.name, "name"),
    email: requiredText(row.email, "email"),
    area: requiredText(row.area, "area"),
    areaKey: requiredText(row.areaKey, "areaKey"),
    workplace: requiredText(row.workplace, "workplace"),
    role: requiredText(row.role, "role"),
    department: requiredText(row.department, "department"),
    mrExperience: requiredText(row.mrExperience, "mrExperience"),
    labelIds: row.labelIds.map((value) => requiredText(value, "labelId")),
    isActive: row.isActive,
    identityBound: row.identityBound,
    updatedAt: optionalText(row.updatedAt, "updatedAt"),
    updatedBy: optionalText(row.updatedBy, "updatedBy"),
  };
}

function parseLabel(row) {
  if (!Number.isInteger(row?.usageCount) || row.usageCount < 0 || typeof row.isActive !== "boolean") {
    throw new Error("ラベルの行形式が不正です");
  }
  return {
    labelId: requiredText(row.labelId, "labelId"),
    name: requiredText(row.name, "label name"),
    color: requiredText(row.color, "label color"),
    usageCount: row.usageCount,
    isActive: row.isActive,
    updatedAt: optionalText(row.updatedAt, "updatedAt"),
    updatedBy: optionalText(row.updatedBy, "updatedBy"),
  };
}

export function managementUsersModel(payload) {
  return rowsModel(payload, "users", parseUser, "管理ユーザー");
}

export function managementLabelsModel(payload) {
  return rowsModel(payload, "labels", parseLabel, "ラベル");
}

function textList(value, field) {
  if (!Array.isArray(value)) throw new Error(`管理選択肢の${field}が不正です`);
  return [...new Set(value.map((item) => requiredText(item, field)))];
}

export function managementMetadataModel(payload) {
  if (!payload || typeof payload !== "object") throw new Error("管理選択肢の形式が不正です");
  const model = {
    areas: textList(payload.areas, "areas"),
    workplaces: textList(payload.workplaces, "workplaces"),
    roles: textList(payload.roles, "roles"),
    departments: textList(payload.departments, "departments"),
    labelColors: textList(payload.labelColors, "labelColors"),
  };
  if (!model.areas.length || !model.departments.length || !model.labelColors.length) {
    throw new Error("管理選択肢に必須項目がありません");
  }
  return model;
}
