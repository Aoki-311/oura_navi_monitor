# 監視フロントエンド API 契約

## 1. 目的

本ドキュメントは、OurA Navi Monitor 次期フロントエンドが参照する API payload を固定するための契約文書です。

フロントエンドは BigQuery や Firestore を直接意識せず、以下の API を通じて表示・検索・エクスポートを行います。

## 2. 共通仕様

### 2.1 共通 query parameters

| Parameter | 型 | 説明 |
| --- | --- | --- |
| `preset` | string | `today`, `last_6h`, `last_12h`, `last_3d`, `last_7d`, `last_14d`, `last_30d`, `last_60d`, `all`, `custom` |
| `start` | string | custom range start。ISO datetime。 |
| `end` | string | custom range end。ISO datetime。 |
| `timezone` | string | 既定 `Asia/Tokyo`。 |

### 2.2 共通 response metadata

```json
{
  "window": {
    "preset": "today",
    "start": "2026-05-13T00:00:00+09:00",
    "end": "2026-05-14T00:00:00+09:00",
    "timezone": "Asia/Tokyo"
  },
  "meta": {
    "generatedAt": "2026-05-13T12:00:00+09:00",
    "cacheHit": false,
    "dataDelaySec": 30,
    "metricStatus": {
      "answerSuccessRate": "proxy"
    }
  }
}
```

## 3. `GET /api/metrics/system-dashboard`

`ダッシュボード` の主要データを返します。

`meta.metricStatus.answerSuccessRate` は回答成功率の集計状態です。`official` は action event 連携後の正式集計、`proxy` は過去データ等の暫定集計、`mixed` は対象期間内に正式集計と暫定集計が混在している状態、`unknown` は対象期間に回答データがない状態を表します。

旧 `GET /api/metrics/dashboard` は互換用 alias です。新規フロントエンドは必ず `/api/metrics/system-dashboard` を使用してください。旧 endpoint の response には `meta.deprecated=true` と `meta.replacementEndpoint="/api/metrics/system-dashboard"` が入ります。

### 3.1 Response

```json
{
  "kpis": {
    "activeUserCount": 123,
    "answerSuccessRate": 0.94,
    "lowCoverageRate": 0.08,
    "errorRate": 0.01,
    "p95LatencyMs": 2400
  },
  "usageTrend": [
    {
      "date": "2026-05-13",
      "activeUserCount": 36,
      "messageCount": 420
    }
  ],
  "activityDistribution": {
    "totalUserCount": 12856,
    "segments": [
      {"label": "高アクティブ", "count": 1842, "rate": 0.1433},
      {"label": "中アクティブ", "count": 2840, "rate": 0.2208},
      {"label": "低アクティブ", "count": 3756, "rate": 0.2920},
      {"label": "休眠ユーザー", "count": 4418, "rate": 0.3439}
    ]
  },
  "environmentMode": {
    "requestByHour": [
      {"hour": "00:00", "requestCount": 12}
    ],
    "deviceDistribution": [
      {"label": "PC", "count": 100, "rate": 0.7},
      {"label": "モバイル", "count": 40, "rate": 0.28},
      {"label": "不明", "count": 3, "rate": 0.02}
    ],
    "modeDistribution": [
      {"label": "社内モード", "count": 90, "rate": 0.63},
      {"label": "Web検索モード", "count": 53, "rate": 0.37}
    ]
  },
  "answerQuality": {
    "answerability": [],
    "usability": [],
    "deliveryReadiness": [],
    "evidenceSufficiency": []
  },
  "followup": {
    "recognizedCount": 20,
    "successCount": 16,
    "successRate": 0.8,
    "explicitCorrectionCount": 2,
    "clarificationRequiredCount": 3
  }
}
```

## 4. `GET /api/metrics/answer-quality`

`回答品質` 画面用の詳細集計を返します。

```json
{
  "summary": {
    "answerCount": 100,
    "answerSuccessRate": 0.94,
    "lowCoverageRate": 0.08,
    "averageCoverageScore": 0.82,
    "structuredLedRate": 0.31
  },
  "distributions": {
    "answerability": [],
    "usability": [],
    "deliveryReadiness": [],
    "evidenceSufficiency": []
  },
  "riskReasons": [
    {"label": "根拠不足", "count": 8, "rate": 0.08}
  ]
}
```

## 5. `GET /api/metrics/followup`

`追問分析` 画面用の集計を返します。

```json
{
  "summary": {
    "recognizedCount": 20,
    "successCount": 16,
    "successRate": 0.8,
    "explicitCorrectionCount": 2,
    "clarificationRequiredCount": 3
  },
  "funnel": [
    {"label": "追問認識", "count": 20},
    {"label": "追問成功", "count": 16},
    {"label": "明示的な訂正", "count": 2},
    {"label": "確認が必要な追問", "count": 3}
  ],
  "reasonBreakdown": [
    {"label": "失敗理由", "value": "missing_anchor", "count": 4}
  ]
}
```

## 6. `GET /api/metrics/users`

`ユーザー監視一覧` のデータを返します。

### 6.1 Query parameters

| Parameter | 型 | 説明 |
| --- | --- | --- |
| `activity` | string | `high`, `middle`, `low`, `dormant`, empty |
| `q` | string | user ID または email 検索 |
| `limit` | int | 既定 100 |
| `cursor` | string | pagination cursor |

### 6.2 Response

```json
{
  "users": [
    {
      "userId": "user-1",
      "userEmail": "user@example.com",
      "userIdHash": "hash",
      "lastActiveAtJst": "2026-05-13 10:20:00",
      "activeDays7": 3,
      "messageCount7d": 12,
      "coverageRate": 0.92,
      "badFeedbackRate": 0.03,
      "activityLevel": "高アクティブ"
    }
  ],
  "page": {
    "nextCursor": ""
  }
}
```

## 7. `GET /api/metrics/users/{user_id}`

`ユーザー詳細` の軽量データを返します。会話本文や message 明細はこの API では返さず、必要時に `/api/trace/messages` で懒加载します。

### 7.1 Query parameters

| Parameter | 型 | 説明 |
| --- | --- | --- |
| `preset` | string | `today`, `last_3d`, `last_7d`, `last_14d`, `last_30d`, `last_60d`, `all` |
| `start` / `end` | string | custom range |
| `conversation_limit` | int | 会話一覧の返却件数。既定 50、最大 200 |
| `conversation_cursor` | string | 次ページ取得用 cursor |
| `include_hidden` | bool | hidden conversation を含めるか |
| `include_messages` | bool | 互換用。既定 false。true でも message 明細はこの response には含めない |

```json
{
  "meta": {
    "generatedAt": "2026-05-13T10:20:00+09:00",
    "cacheHit": false,
    "dataDelaySec": null,
    "metricStatus": {
      "answerSuccessRate": "proxy",
      "badFeedbackRate": "pending"
    }
  },
  "user": {
    "userId": "user-1",
    "userEmail": "user@example.com",
    "activityLevel": "高アクティブ",
    "lastActiveAtJst": "2026-05-13 10:20:00"
  },
  "summary": {
    "messageCount": 12,
    "answerSuccessRate": 0.94,
    "lowCoverageRate": 0.08,
    "badFeedbackRate": 0.03,
    "followupCount": 4
  },
  "trend": [
    {"date": "2026-05-13", "messageCount": 3, "answerSuccessRate": 1.0, "lowCoverageRate": 0.0}
  ],
  "modeDistribution": [],
  "answerQualityDistribution": {},
  "followup": {},
  "conversations": [
    {
      "conversationId": "conv-1",
      "title": "タイトル",
      "mode": "社内モード",
      "visibility": "active",
      "createdAtJst": "2026-05-13 09:00:00",
      "updatedAtJst": "2026-05-13 10:20:00",
      "messageCount": 6,
      "integrityState": "ok",
      "isFavorite": false,
      "followupRuntimeSummary": {}
    }
  ],
  "page": {
    "nextCursor": "2026-05-13T10:20:00Z",
    "cursor": ""
  },
  "messageLoading": {
    "endpoint": "/api/trace/messages",
    "includeMessagesInThisResponse": false,
    "includeMessagesRequested": false
  }
}
```

## 8. `GET /api/trace/messages`

`チャット記録確認` の検索結果を返します。

### 8.1 Query parameters

| Parameter | 型 | 説明 |
| --- | --- | --- |
| `conversation_id` | string | conversation ID |
| `trace_id` | string | trace ID |
| `turn_id` | string | turn ID |
| `user_id` | string | user ID |
| `user_email` | string | email |
| `status` | string | `done`, `error`, `aborted`, `streaming` |
| `mode` | string | `internal`, `websearch` |
| `limit` | int | 既定 100 |
| `cursor` | string | 次ページ取得用の不透明 cursor |
| `include_content` | boolean | 既定 `false`。`true` の場合のみ本文原文 `content` を返す |

本文は個人情報・業務情報を含む可能性があるため、通常画面では `contentPreview` のみを返します。管理者が明示的に本文表示または本文出力を選択した場合だけ `include_content=true` を指定します。

### 8.2 Response

```json
{
  "page": {
    "limit": 100,
    "cursor": "",
    "nextCursor": "opaque-cursor"
  },
  "contentPolicy": {
    "includeContent": false,
    "defaultPreviewOnly": true
  },
  "conversations": [
    {
      "conversationId": "conv-1",
      "title": "タイトル",
      "mode": "社内モード",
      "visibility": "active",
      "createdAtJst": "2026-05-13 09:00:00",
      "updatedAtJst": "2026-05-13 10:20:00",
      "messageCount": 6,
      "integrityState": "ok",
      "isFavorite": false,
      "followupRuntimeSummary": {}
    }
  ],
  "messages": [
    {
      "timestampJst": "2026-05-13 10:20:00",
      "role": "ユーザー",
      "roleLabel": "ユーザー",
      "roleRaw": "user",
      "status": "完了",
      "statusLabel": "完了",
      "statusRaw": "done",
      "modeAtSend": "社内モード",
      "modeAtSendLabel": "社内モード",
      "modeAtSendRaw": "internal",
      "deviceClass": "PC",
      "deviceLabel": "PC",
      "deviceClassRaw": "desktop",
      "chatFlowType": "continued_chat",
      "clientOrigin": "typed",
      "feedback": "none",
      "contentPreview": "質問本文のプレビュー",
      "conversationId": "conv-1",
      "traceId": "trace-1",
      "requestId": "req-1",
      "turnId": "turn-1",
      "messageId": "msg-1",
      "tags": ["追問", "回答成功"]
    }
  ],
  "payloadEvents": [
    {
      "eventTsJst": "2026-05-13 10:20:00",
      "eventFamily": "ask_audit_json",
      "schemaVersion": "phase2.audit.v1",
      "conversationId": "conv-1",
      "traceId": "trace-1",
      "requestId": "req-1",
      "turnId": "turn-1",
      "messageId": "msg-1",
      "userId": "user-1",
      "conversationTurnKey": "conv-1#turn-1",
      "conversationMessageKey": "conv-1#msg-1",
      "traceRequestKey": "trace-1#req-1"
    }
  ]
}
```

`payloadEvents` は BigQuery 側の monitor event 投影です。`trace_id` または `turn_id` のみで検索された場合、まずこの投影から `user_id` / `conversation_id` / `turn_id` / `message_id` 候補を解決し、Firestore message と join します。複数候補が返る場合も、先頭 1 件に固定せず候補ごとに精密検索します。

## 9. `GET /api/metrics/schema-health`

`データ健全性` のデータを返します。

```json
{
  "events": [
    {
      "eventFamily": "ask_audit_json",
      "schemaVersion": "phase2.audit.v1",
      "eventCount": 100,
      "requiredFieldMissingCount": 0,
      "schemaMismatchCount": 0
    }
  ],
  "joinHealth": {
    "answerRowCount": 100,
    "joinedMessageCount": 96,
    "joinRate": 0.96,
    "followupUnjoinedCount": 2,
    "coverageGapJoinRate": 0.9
  },
  "dataDelay": {
    "p95Sec": 45
  }
}
```

## 10. `POST /api/export/jobs`

エクスポート設定モーダルから呼び出す API です。新フロントエンドは legacy CSV endpoint を直接呼び出しません。`出力データ` ごとに固定列を出力し、画面上の選択肢と実際の出力列がずれないようにします。

### 10.1 Request

```json
{
  "scope": "all",
  "preset": "last_7d",
  "start": "",
  "end": "",
  "outputData": "ユーザー監視一覧",
  "filters": {
    "activity": "high",
    "q": "",
    "userId": "",
    "userEmail": ""
  }
}
```

`scope` は `all` または `user` です。`scope=all` は全ユーザー範囲、`scope=user` は現在表示中または指定ユーザー範囲です。`scope=user` の場合は `filters.userId` または `filters.userEmail` が必須です。

`outputData` は以下のみを受け付けます。

| scope | outputData |
| --- | --- |
| `all` | `ユーザー監視一覧`, `メッセージ明細` |
| `user` | `ユーザーサマリー`, `メッセージ明細` |

`preset=custom` の場合は `start` と `end` を指定します。custom range は message export にも必ず適用されます。フロントエンドでは終了日を含めるため、`end` は終了日の翌日 00:00 JST を排他的境界として送信します。

### 10.2 Response

```json
{
  "jobId": "export-1",
  "status": "ready",
  "downloadUrl": "/api/export/jobs/export-1/download",
  "expiresAt": "2026-05-13T13:00:00+09:00"
}
```

第一版では同期生成して `status=ready` を返します。API 形状は job として固定し、将来 GCS 一時ファイルや非同期化へ移行できるようにします。

### 10.3 Fixed CSV Columns

`ユーザー監視一覧`:

```text
ユーザーID
メールアドレス
最終利用日時
直近7日利用日数
直近7日メッセージ数
根拠カバレッジ率
低評価率
活性度区分
```

`ユーザーサマリー`:

```text
ユーザーID
メールアドレス
最終利用日時
活性度区分
メッセージ数
回答成功率
低カバレッジ率
低評価率
追問数
```

`メッセージ明細`:

```text
user_id
user_email
conversation_id
title
created_at
役割
message原文
質問カテゴリ
モード
デバイス
フィードバック
```

`メッセージ明細` は dashboard から出力する場合もユーザー詳細から出力する場合も、必ず `user_id` と `user_email` を先頭に含めます。ユーザー詳細からの出力は現在表示中のユーザーに限定します。

### 10.4 Message Content Safety

`メッセージ明細` は message 原文を含むため、フロントエンドで二次確認を出し、バックエンドでは export audit log を必ず出力します。audit log には admin email、scope、outputData、期間、filters、row count、job id を含めます。

### 10.5 Legacy Export Endpoint Policy

新フロントエンドのエクスポートは必ず `POST /api/export/jobs` を使用します。以下の旧 CSV endpoint は、メッセージ原文やユーザー一覧を audit log なしで直接出力できるため、`410 Gone` を返します。

```text
GET /api/export/user-monitoring.csv
GET /api/export/users.csv
GET /api/export/conversations.csv
GET /api/export/messages.csv
```
