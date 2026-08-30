export function exportJobModel(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("CSV作成結果が不正です。");
  const jobId = typeof payload.jobId === "string" ? payload.jobId.trim() : "";
  const filename = typeof payload.filename === "string" ? payload.filename.trim() : "";
  const expiresAt = typeof payload.expiresAt === "string" ? payload.expiresAt.trim() : "";
  if (!/^[A-Za-z0-9_-]{1,120}$/.test(jobId) || payload.status !== "ready") throw new Error("CSV作成結果が不正です。");
  if (!filename || filename.length > 200 || /[\\/\r\n]/.test(filename) || !filename.toLowerCase().endsWith(".csv")) throw new Error("CSVファイル名が不正です。");
  if (!Number.isInteger(payload.rowCount) || payload.rowCount < 0) throw new Error("CSV行数が不正です。");
  const expiresAtMs = Date.parse(expiresAt);
  if (!expiresAt || Number.isNaN(expiresAtMs) || expiresAtMs <= Date.now()) throw new Error("CSV有効期限が不正です。");
  const expectedDownloadUrl = `/api/export/jobs/${encodeURIComponent(jobId)}/download`;
  if (payload.downloadUrl !== expectedDownloadUrl) throw new Error("CSVのダウンロード先が不正です。");
  return { jobId, status: "ready", filename, rowCount: payload.rowCount, expiresAt, downloadUrl: expectedDownloadUrl };
}
