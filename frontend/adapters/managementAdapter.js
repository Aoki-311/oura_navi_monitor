function requiredText(value, field) {
  if (typeof value !== "string" || !value.trim()) throw new Error(`管理データの${field}が不正です`);
  return value;
}

function optionalText(value, field) {
  if (value == null) return "";
  if (typeof value !== "string") throw new Error(`管理データの${field}が不正です`);
  return value;
}

export function managementUsersModel(payload) {
  if (!payload || !Array.isArray(payload.users)) throw new Error("管理ユーザーの形式が不正です");
  return payload.users.map((row) => {
    if (!Array.isArray(row.labelIds) || typeof row.isActive !== "boolean") throw new Error("管理ユーザーの行形式が不正です");
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
      updatedAt: optionalText(row.updatedAt, "updatedAt"),
      updatedBy: optionalText(row.updatedBy, "updatedBy"),
    };
  });
}

export function managementLabelsModel(payload) {
  if (!payload || !Array.isArray(payload.labels)) throw new Error("ラベルの形式が不正です");
  return payload.labels.map((row) => {
    if (!Number.isInteger(row.usageCount) || row.usageCount < 0 || typeof row.isActive !== "boolean") throw new Error("ラベルの行形式が不正です");
    return {
      labelId: requiredText(row.labelId, "labelId"),
      name: requiredText(row.name, "label name"),
      color: requiredText(row.color, "label color"),
      usageCount: row.usageCount,
      isActive: row.isActive,
      updatedAt: optionalText(row.updatedAt, "updatedAt"),
      updatedBy: optionalText(row.updatedBy, "updatedBy"),
    };
  });
}
