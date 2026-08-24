export function numberOrNull(value) {
  if (value === undefined || value === null || value === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) throw new Error("数値データが不正です");
  return n;
}

export function countOrZero(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) throw new Error("件数データが不正です");
  return Math.trunc(n);
}

export function displayCount(value) {
  return countOrZero(value).toLocaleString("ja-JP");
}

export function displayRate(value, digits = 1) {
  const n = numberOrNull(value);
  return n === null ? "-" : `${(n * 100).toFixed(digits)}%`;
}

export function displayMs(value) {
  const n = numberOrNull(value);
  return n === null ? "-" : `${Math.round(n).toLocaleString("ja-JP")} ms`;
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
