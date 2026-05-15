export const PRESET_LABELS = {
  today: "今日",
  last_6h: "直近6時間",
  last_12h: "直近12時間",
  last_3d: "過去3日",
  last_7d: "過去7日",
  last_14d: "過去14日",
  last_30d: "過去30日",
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
  high: "直近3日内メッセージ送信が3回以上",
  middle: "直近7日内メッセージ送信が1-2回",
  low: "直近14日内メッセージ送信が1回以上、かつ中/高に該当しない",
  dormant: "直近14日内メッセージ送信が0回",
};

export const KPI_HELP = {
  activeUserCount: "選択した期間内に実際にチャットを利用したユーザー数です。",
  answerSuccessRate:
    "エラー表示がなく、ユーザーからの回答再生成・回答強化・修正要求・低評価が確認されていない回答率です。",
  lowCoverageRate: "根拠資料や引用が不足している可能性がある回答の割合です。",
  errorRate: "回答生成や通信処理でエラーになった割合です。",
  p95LatencyMs: "利用者の大半が待つ最大に近い応答時間の目安です。数値が大きいほど体感が遅くなります。",
  activityDistribution: "直近14日間の利用頻度に基づき、ユーザーを高・中・低・休眠に分類しています。",
};

export function labelOf(labels, raw, fallback = "不明") {
  const key = String(raw || "").trim().toLowerCase();
  return labels[key] || String(raw || "").trim() || fallback;
}
