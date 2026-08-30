export const SUMMARY_ROLES = Object.freeze(["本社MR", "コントラクトMR"]);

export function isSummaryRole(value) {
  return SUMMARY_ROLES.includes(value);
}

export function isExactSummaryRoleSet(values) {
  return Array.isArray(values)
    && values.length === SUMMARY_ROLES.length
    && SUMMARY_ROLES.every((role) => values.includes(role));
}
