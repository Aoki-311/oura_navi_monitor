export function numberOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) throw new Error("数値データが不正です");
  return n;
}

export function countOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) throw new Error("件数データが不正です");
  return Math.trunc(n);
}

export function displayCount(value) {
  const count = countOrNull(value);
  return count === null ? "-" : count.toLocaleString("ja-JP");
}

export function displayRate(value, digits = 1) {
  const n = numberOrNull(value);
  return n === null ? "-" : `${(n * 100).toFixed(digits)}%`;
}

export function displayDuration(value) {
  const n = numberOrNull(value);
  if (n === null) return "-";
  const seconds = Math.max(0, n) / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes}分${remainder}秒` : `${minutes}分`;
}

export function measurementCoverage(value) {
  const measured = countOrNull(value?.measuredCount);
  const total = countOrNull(value?.totalCount);
  if (measured === null || total === null) return "計測範囲不明";
  if (total === 0) return "対象回答なし";
  return measured === total ? `全${total.toLocaleString("ja-JP")}件を計測` : `${measured.toLocaleString("ja-JP")} / ${total.toLocaleString("ja-JP")}件を計測`;
}

export function displayNullable(value) {
  if (value === undefined || value === null || value === "") return "-";
  return String(value);
}

export function displayDateTime(value) {
  const text = String(value || "").trim();
  if (!text) return "-";
  const dt = new Date(text);
  if (Number.isNaN(dt.getTime())) return text;
  return dt.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function truncateMiddle(value, keepStart = 10, keepEnd = 6) {
  const text = String(value || "");
  if (text.length <= keepStart + keepEnd + 3) return text || "-";
  return `${text.slice(0, keepStart)}...${text.slice(-keepEnd)}`;
}
