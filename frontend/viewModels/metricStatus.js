export function toMetricStatusBadge(status) {
  const normalized = String(status || "unknown").toLowerCase();
  const map = {
    official: { label: "正式値", tone: "success" },
    proxy: { label: "暫定値", tone: "warning" },
    mixed: { label: "正式値・暫定値混在", tone: "warning" },
    unknown: { label: "データなし", tone: "muted" },
  };
  return map[normalized] || map.unknown;
}
