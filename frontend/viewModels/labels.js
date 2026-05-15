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
  price: "価格関連",
  pricing: "価格関連",
  gpo: "GPO関連",
  product: "製品関連",
  product_lookup: "製品関連",
  product_master: "製品関連",
  product_master_lookup: "製品関連",
  comparison: "比較関連",
  compare: "比較関連",
  evidence: "根拠確認",
  citation: "根拠確認",
  fact: "事実確認",
  fact_check: "事実確認",
  feasibility: "実現性確認",
  risk: "リスク確認",
  mechanism: "仕組み確認",
  benefit: "メリット確認",
  pain: "課題確認",
  switch: "切替検討",
  access_barrier: "アクセス障壁",
  hospital: "施設関連",
  strategy: "戦略・提案関連",
  timeliness: "最新情報・時事関連",
  sales_support: "営業支援",
  business_output: "資料作成",
  workflow: "業務手順",
  workflow_support: "業務手順",
  asset_lookup: "資料検索",
  asset_request: "資料作成・取得",
  source_locator: "根拠確認",
  support: "サポート関連",
  policy_or_rule: "規定・ルール",
  ops_guidance: "運用ガイダンス",
  followup: "連続質問",
  general: "一般質問",
  unknown: "未分類",
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
  return labelOf(QUESTION_CATEGORY_LABELS, raw, "未分類");
}
