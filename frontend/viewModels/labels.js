export const PRESET_LABELS = Object.freeze({
  today: "今日",
  last_3d: "過去3日",
  last_7d: "過去7日",
  last_14d: "過去14日",
  last_30d: "過去30日",
  last_60d: "過去60日",
  all: "全期間",
});

export const QUESTION_CATEGORY_LABELS = Object.freeze({
  product_information: "製品情報・仕様",
  price_product_code: "価格・製品コード",
  comparison_fit_selection: "比較・適合・選定",
  usage_procedure: "使用方法・手順",
  troubleshooting_safety: "トラブル・安全対応",
  sales_proposal: "営業活動・提案作成",
  institution_gpo_market: "医療機関・GPO・市場情報",
  document_search: "資料・文書を探す",
  other_general: "その他・一般質問",
  unclassified: "判定不能",
});

export const ANALYTICS_TASK_LABELS = Object.freeze({
  fact_lookup: "情報確認",
  explanation: "説明依頼",
  comparison_selection: "比較・選定",
  procedure_guidance: "手順確認",
  troubleshooting: "問題解決",
  content_creation: "資料・文面作成",
  source_retrieval: "資料検索",
  market_research: "市場・施設調査",
  other: "その他",
  unclassified: "判定不能",
});

export const ACTIVITY_LABELS = Object.freeze({
  high: "高アクティブ",
  middle: "中アクティブ",
  low: "低アクティブ",
  dormant: "休眠ユーザー",
});

export const ACTIVITY_DEFINITIONS = Object.freeze({
  high: "直近3日で有効な質問が3回以上",
  middle: "高アクティブ以外で、直近7日の有効な質問が1〜2回",
  low: "高・中以外で、直近14日に有効な質問が1回以上",
  dormant: "直近14日に有効な質問が0回",
});

export const MODE_LABELS = Object.freeze({ internal: "社内モード", websearch: "Web検索モード", unknown: "不明" });
export const DEVICE_LABELS = Object.freeze({ desktop: "PC", mobile: "モバイル", unknown: "不明" });
export const DEPARTMENTS = Object.freeze(["DM専任", "ヘルスケア本社", "DM本社", "管理者"]);

export function questionCategoryLabel(value) {
  const key = String(value || "").trim();
  if (!Object.hasOwn(QUESTION_CATEGORY_LABELS, key)) throw new Error(`未対応の質問タイプ: ${key || "(空)"}`);
  return QUESTION_CATEGORY_LABELS[key];
}

export function analyticsTaskLabel(value) {
  const key = String(value || "").trim();
  if (!Object.hasOwn(ANALYTICS_TASK_LABELS, key)) throw new Error(`未対応の分析タスク: ${key || "(空)"}`);
  return ANALYTICS_TASK_LABELS[key];
}
