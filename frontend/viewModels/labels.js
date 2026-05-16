export const PRESET_LABELS = {
  today: "今日",
  last_6h: "直近6時間",
  last_12h: "直近12時間",
  last_3d: "過去3日",
  last_7d: "過去7日",
  last_14d: "過去14日",
  last_30d: "過去30日",
  last_60d: "過去60日",
  all: "全部",
  custom: "カスタム",
};

export const MODE_LABELS = {
  internal: "社内モード",
  websearch: "Web検索モード",
  standard: "標準モード",
  deepthinking: "深考モード",
  other: "その他",
};

export const DEVICE_LABELS = {
  desktop: "PC",
  mobile: "モバイル",
  unknown: "不明",
};

export const QUESTION_CATEGORY_LABELS = {
  product_explanation: "製品説明",
  sales_approach: "営業手法",
  troubleshooting: "トラブル対応",
  product_price: "製品価格関連",
  hospital_gpo: "病院・GPO関連",
  topic_ideation: "ネタ探し",
  price: "製品価格関連",
  pricing: "製品価格関連",
  price_lookup: "製品価格関連",
  gpo: "病院・GPO関連",
  hospital: "病院・GPO関連",
  org_info: "病院・GPO関連",
  market_intelligence: "ネタ探し",
  product: "製品説明",
  product_lookup: "製品説明",
  product_master: "製品説明",
  product_master_lookup: "製品説明",
  product_applicability: "製品説明",
  comparison: "製品説明",
  compare: "製品説明",
  fact: "製品説明",
  fact_check: "製品説明",
  strategy: "営業手法",
  promotion: "営業手法",
  human_approach: "営業手法",
  business_output: "営業手法",
  content_asset_generation: "営業手法",
  sales_script_generation: "営業手法",
  incident_troubleshooting: "トラブル対応",
  safety_incident_response: "トラブル対応",
  general: "ネタ探し",
  unknown: "ネタ探し",
};

export const ROLE_LABELS = {
  user: "ユーザー",
  assistant: "AI回答",
  system: "システム",
};

export const STATUS_LABELS = {
  done: "完了",
  error: "エラー",
  aborted: "取消し",
  streaming: "生成中",
};

export const QUALITY_LABELS = {
  fully_answerable: "十分に回答可能",
  partially_answerable: "一部回答可能",
  not_answerable: "回答困難",
  clarification_blocked: "確認が必要",
  ready: "利用可能",
  bounded: "条件付き利用可能",
  not_ready: "利用困難",
  clarify_first: "確認後に利用",
  sufficient: "十分",
  partial: "一部不足",
  insufficient: "不足",
  unknown: "不明",
};

export const ACTIVITY_DEFINITIONS = {
  high: "直近3日内にメッセージ送信が3回以上あるユーザーです。",
  middle: "直近7日内にメッセージ送信が1〜2回あるユーザーです。",
  low: "直近14日内にメッセージ送信が1回以上あり、高・中アクティブに該当しないユーザーです。",
  dormant: "直近14日内にメッセージ送信がないユーザーです。",
};

export const KPI_HELP = {
  activeUserCount: "選択した期間内に実際にチャットを利用したユーザー数です。",
  answerSuccessRate:
    "エラー表示がなく、ユーザーからの回答再生成・回答強化・修正要求・低評価が確認されていない回答率です。",
  lowCoverageRate: "根拠資料や引用が不足している可能性がある回答の割合です。",
  errorRate: "回答生成や通信処理でエラーになった割合です。",
  p95LatencyMs: "利用者の大半が待つ最大に近い応答時間の目安です。数値が大きいほど体感が遅くなります。",
  activityDistribution: "選択した表示期間内の利用頻度に基づき、ユーザーを高・中・低・休眠に分類しています。",
  questionCategory: "ユーザーの質問内容を、回答ログの分類情報に基づいて大まかな業務カテゴリに分けたものです。",
  usability: "回答内容が管理者や利用者にとって実際に使える状態かを示します。",
  evidenceSufficiency: "回答に必要な根拠資料、引用、構造化データが十分に揃っているかを示します。",
};

export function labelOf(labels, raw, fallback = "不明") {
  const key = String(raw || "").trim().toLowerCase();
  return labels[key] || String(raw || "").trim() || fallback;
}

export function questionCategoryLabel(raw) {
  return labelOf(QUESTION_CATEGORY_LABELS, raw, "ネタ探し");
}
