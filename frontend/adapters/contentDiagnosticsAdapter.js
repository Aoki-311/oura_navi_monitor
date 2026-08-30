function unavailable(issue) {
  return {
    state: "degraded",
    labelCatalogStatus: "unavailable",
    rosterStatus: "unavailable",
    rosterIsolatedCount: null,
    rosterIssueCounts: {},
    rosterDiagnosticsAvailable: false,
    issues: [issue],
    notice: "ラベル情報と名簿診断情報の状態を確認できません。利用状況は表示しています。",
    exportAvailable: false,
  };
}

export function contentDiagnosticsModel(payload) {
  const value = payload?.contentDiagnostics;
  if (value == null) return unavailable("missing_content_diagnostics");
  if (!value || typeof value !== "object" || Array.isArray(value)) return unavailable("invalid_content_diagnostics");
  const status = ["available", "partial", "unavailable", "not_applicable"].includes(value.labelCatalogStatus)
    ? value.labelCatalogStatus
    : "unavailable";
  const inputIssuesValid = Array.isArray(value.issues)
    && value.issues.every((issue) => typeof issue === "string" && issue.trim());
  const issues = inputIssuesValid ? [...value.issues] : ["invalid_content_diagnostics"];
  const rosterFieldsPresent = ["rosterStatus", "rosterIsolatedCount", "rosterIssueCounts"]
    .every((field) => Object.hasOwn(value, field));
  const issueCountsObject = value.rosterIssueCounts
    && typeof value.rosterIssueCounts === "object"
    && !Array.isArray(value.rosterIssueCounts);
  const rosterIssueCounts = issueCountsObject
    ? Object.fromEntries(Object.entries(value.rosterIssueCounts).filter(([key, count]) => (
      typeof key === "string" && key.trim() && Number.isInteger(count) && count > 0
    )))
    : {};
  const issueCountsValid = issueCountsObject
    && Object.keys(rosterIssueCounts).length === Object.keys(value.rosterIssueCounts).length;
  const rosterIsolatedCount = Number.isInteger(value.rosterIsolatedCount) && value.rosterIsolatedCount >= 0
    ? value.rosterIsolatedCount
    : null;
  const rosterStatus = ["available", "partial"].includes(value.rosterStatus)
    ? value.rosterStatus
    : "unavailable";
  const rosterStateConsistent = rosterFieldsPresent
    && issueCountsValid
    && rosterIsolatedCount != null
    && (
      (rosterStatus === "available" && rosterIsolatedCount === 0 && Object.keys(rosterIssueCounts).length === 0)
      || (rosterStatus === "partial" && rosterIsolatedCount > 0 && Object.keys(rosterIssueCounts).length > 0)
    )
    && Object.keys(rosterIssueCounts).every((key) => issues.includes(`roster_${key}`));
  const rosterDiagnosticsAvailable = rosterStateConsistent;
  const stateValid = value.state === "complete" || value.state === "degraded";
  const labelDegraded = status === "partial" || status === "unavailable";
  const rosterDegraded = !rosterDiagnosticsAvailable || rosterStatus !== "available";
  const shouldBeDegraded = labelDegraded || rosterDegraded || issues.length > 0;
  const stateConsistent = stateValid && value.state === (shouldBeDegraded ? "degraded" : "complete");
  const notices = [];
  if (status === "unavailable") notices.push("ラベル情報を取得できません。利用状況は表示しています。");
  else if (status === "partial") notices.push("一部のラベル情報を除外しました。利用状況は表示しています。");
  if (!rosterDiagnosticsAvailable) notices.push("名簿診断情報を確認できません。利用状況は表示しています。");
  else if (rosterStatus === "partial") notices.push(`名簿データの不備により ${rosterIsolatedCount}件を除外しました。残りの利用状況は表示しています。`);
  if (!stateConsistent) notices.push("診断情報の整合性を確認できないためCSV出力を停止しています。");
  const degraded = shouldBeDegraded || !stateConsistent;
  return {
    state: degraded ? "degraded" : "complete",
    labelCatalogStatus: status,
    rosterStatus,
    rosterIsolatedCount,
    rosterIssueCounts,
    rosterDiagnosticsAvailable,
    issues: [...new Set([
      ...issues,
      ...(!rosterFieldsPresent ? ["missing_roster_diagnostics"] : []),
      ...(rosterFieldsPresent && !rosterStateConsistent ? ["invalid_roster_diagnostics"] : []),
      ...(!stateConsistent ? ["inconsistent_content_diagnostics"] : []),
    ])],
    notice: [...new Set(notices)].join(" "),
    exportAvailable: !degraded,
  };
}
