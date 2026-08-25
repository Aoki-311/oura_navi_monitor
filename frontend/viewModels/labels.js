export const PRESET_LABELS = Object.freeze({
  today: "今日",
  last_3d: "過去3日",
  last_7d: "過去7日",
  last_14d: "過去14日",
  last_30d: "過去30日",
  last_60d: "過去60日",
  all: "全期間",
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
