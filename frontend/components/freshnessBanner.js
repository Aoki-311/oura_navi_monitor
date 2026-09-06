import { escapeHtml } from "./dom.js";

export function renderFreshnessBanner(target, freshness, quality = null, metadataIssues = []) {
  if (!target) return;
  const source = quality?.sourcePipeline;
  const messages = [];
  if (!freshness || freshness.state === "unknown") messages.push("更新情報を確認できません。表示中の集計値は保持しています。");
  else if (freshness.state === "stale") messages.push("更新が遅れています。直近の利用はまだ含まれていない場合があります。");
  if (source?.state === "blocked") messages.push("更新できませんでした。前回の集計を表示しています。");
  if (Array.isArray(metadataIssues) && metadataIssues.length) messages.push("一部の情報を確認できません。表示できる集計は保持しています。");
  target.dataset.state = freshness?.state || "unknown";
  target.dataset.qualityState = source?.state || "not_applicable";
  target.hidden = messages.length === 0;
  target.innerHTML = messages.map((message) => `<span>${escapeHtml(message)}</span>`).join("");
}
