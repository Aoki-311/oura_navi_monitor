import { escapeHtml } from "./dom.js";
import { displayCount, displayDateTime } from "../viewModels/formatters.js";

export function renderFreshnessBanner(target, freshness, quality = null) {
  if (!target) return;
  const cadenceHours = freshness.refreshCadenceMinutes / 60;
  const through = freshness.dataThrough ? displayDateTime(freshness.dataThrough) : "未取得";
  const next = displayDateTime(freshness.nextPlannedRefreshAt);
  const schedule = `データは${cadenceHours}時間ごとに更新します（処理待ち ${freshness.expectedDelayMinutes}分）。反映済み: ${through}／次回予定: ${next}`;
  const stale = freshness.state === "stale"
    ? `更新が遅れています。データは ${through} までです。これ以降の 0 や空欄は「利用なし」を意味しません。`
    : freshness.state === "unknown"
      ? "公開済みデータの時刻を確認できないため、0件表示を利用なしとは判断できません。"
      : "";
  const isolated = quality?.isolatedEventCount > 0
    ? `利用回数と回答結果は集計済みですが、${displayCount(quality.isolatedEventCount)}件は分類・質問種類・製品のいずれかを隔離し、該当グラフから除外しています。`
    : "";
  const source = quality?.sourcePipeline;
  const sourceIssue = source?.state === "blocked" && source.batchBlockingFailureCount > 0
    ? `最新更新は品質チェック ${displayCount(source.batchBlockingFailureCount)}件で停止し、事実データを公開していません。画面は直前の成功データを表示しています。`
    : source?.state === "blocked"
      ? `最新更新が失敗しました${source.latestRunErrorCode ? `（${source.latestRunErrorCode}）` : ""}。画面は直前の成功データを表示しています。`
    : source?.state === "unknown"
      ? "公開 run の取込品質を確認できません。"
      : source?.quarantinedEventCount > 0
        ? `取込条件を満たさない元イベント ${displayCount(source.quarantinedEventCount)}件を、この公開 run の集計から除外しました。`
        : "";
  const repaired = source && (source.deduplicatedDeliveryCount || source.repairedDuplicateFactCount)
    ? `重複配信 ${displayCount(source.deduplicatedDeliveryCount)}件を統合し、既存の重複ファクト ${displayCount(source.repairedDuplicateFactCount)}件を修復しました。`
    : "";
  target.dataset.state = freshness.state;
  target.dataset.qualityState = source?.state || "not_applicable";
  target.innerHTML = `<strong>${escapeHtml(schedule)}</strong>${stale ? `<span>${escapeHtml(stale)}</span>` : ""}${sourceIssue ? `<span>${escapeHtml(sourceIssue)}</span>` : ""}${repaired ? `<span>${escapeHtml(repaired)}</span>` : ""}${isolated ? `<span>${escapeHtml(isolated)}</span>` : ""}`;
}
