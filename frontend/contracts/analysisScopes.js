export const SUMMARY_ROLES = Object.freeze(["社員MR", "本社MR", "コントラクトMR"]);

export function canonicalDepartment(value) {
  return value === "DM専任" ? "MR(DM)" : value;
}

export function isSummaryRole(value) {
  return SUMMARY_ROLES.includes(value);
}

export function isSummaryUser(row) {
  return isSummaryRole(row?.role) && ["MR(DM)", "MR(HCS)"].includes(canonicalDepartment(row?.department));
}

export function isExactSummaryRoleSet(values) {
  return Array.isArray(values)
    && values.length === SUMMARY_ROLES.length
    && SUMMARY_ROLES.every((role) => values.includes(role));
}
