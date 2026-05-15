# 監視指標契約

## 1. 目的

本ドキュメントは、OurA Navi Monitor のフロントエンドに表示する指標の定義、計算式、期間口径、表示名を固定するための契約文書です。

同じ指標は、ダッシュボード、ユーザー監視一覧、ユーザー詳細、エクスポートで同じ計算口径を使用します。

## 2. 共通ルール

| 項目 | ルール |
| --- | --- |
| 時刻 | 画面表示と日次集計は日本時間を基準にする。 |
| 期間 | `今日`, `直近6時間`, `直近12時間`, `過去3日`, `過去7日`, `過去14日`, `過去30日`, `カスタム` を基本とする。 |
| ユーザー主キー | 分析は `user_id_hash`、管理者表示は `user_id` / `user_email` を使う。 |
| メッセージ対象 | 原則として user / assistant message の両方を保持し、回答品質指標は assistant answer を対象にする。 |
| 後追い更新 | feedback、再生成、強化、修正要求は後から入るため、回答成功率は再集計可能にする。 |

## 3. KPI 指標

### 3.1 `アクティブユーザー数`

| 項目 | 内容 |
| --- | --- |
| 管理者向け説明 | 選択した期間内に実際にチャットを利用したユーザー数です。 |
| 計算式 | 選択期間内に message または ask activity がある distinct user 数。 |
| 主な source | Firestore message, request_user_metric, ask events |
| 注意点 | Bot / health check / system request は除外する。 |

### 3.2 `回答成功率`

| 項目 | 内容 |
| --- | --- |
| 管理者向け説明 | エラー表示がなく、ユーザーからの回答再生成・回答強化・修正要求・低評価が確認されていない回答の割合です。 |
| 分母 | 選択期間内の assistant answer 数。 |
| 分子 | 分母のうち、失敗条件に該当しない answer 数。 |
| 失敗条件 | error status、message error、再生成要求、回答強化要求、修正要求、bad feedback。 |
| 計算式 | `successful_answer_count / assistant_answer_count` |
| 注意点 | feedback が後から付いた場合、過去期間の値も再計算する。 |

#### 集計状態 `answer_success_metric_status`

| 値 | 定義 |
| --- | --- |
| `official` | `lcs-rag-app` が answer action event 対応 revision に正式切替された後の回答です。ユーザーが低評価を押していない場合も、再生成・強化・修正・低評価の捕捉対象として扱います。 |
| `proxy` | 正式切替前の過去回答です。action event が完全ではないため、暫定集計として扱います。 |
| `mixed` | 選択期間内に `official` と `proxy` が混在しています。 |
| `unknown` | 選択期間内に回答データがありません。 |

現在の official cutover は `2026-05-15T03:59:21Z` です。

### 3.3 `低カバレッジ率`

| 項目 | 内容 |
| --- | --- |
| 管理者向け説明 | 根拠資料や引用が不足している可能性がある回答の割合です。 |
| 分母 | 選択期間内の assistant answer 数。 |
| 低カバレッジ条件 | `coverage_gap_workitem_json` がある、`citation_count = 0`、`evidence_sufficiency = insufficient`、`coverage_score < threshold` のいずれか。 |
| 初期 threshold | `coverage_score < 0.60` |
| 計算式 | `low_coverage_answer_count / assistant_answer_count` |

### 3.4 `エラー率`

| 項目 | 内容 |
| --- | --- |
| 管理者向け説明 | 回答生成や通信処理でエラーになった割合です。 |
| 分母 | 選択期間内の主要 request または answer attempt。 |
| 分子 | HTTP 5xx、message status error、stream error、aborted error を含む失敗数。 |
| 計算式 | `error_count / monitored_attempt_count` |
| 備考 | HTTP error と message error の内訳は export に含める。 |

### 3.5 `P95応答時間`

| 項目 | 内容 |
| --- | --- |
| 管理者向け説明 | 利用者の大半が待つ最大に近い応答時間の目安です。数値が大きいほど体感が遅くなります。 |
| 計算式 | 選択期間内 latency の 95 percentile。 |
| 主な source | Cloud Run request logs, answer/generation metrics |
| 表示単位 | 秒。 |

## 4. 利用推移

### 4.1 `利用推移`

| 表示 | 計算式 |
| --- | --- |
| `アクティブユーザー数` | 日別 distinct active user 数。 |
| `メッセージ数` | 日別 message 数。 |

初期表示は7日間、選択肢は 7日、14日、30日とします。

### 4.2 `活性度分布（14日）`

| 活性度区分 | 定義 |
| --- | --- |
| `高アクティブ` | 直近3日内メッセージ送信が3回以上 |
| `中アクティブ` | 直近7日内メッセージ送信が1-2回 |
| `低アクティブ` | 直近14日内メッセージ送信が1回以上、かつ中/高に該当しない |
| `休眠ユーザー` | 直近14日内メッセージ送信が0回 |

ユーザー監視一覧の `活性度区分` と同じ定義を使用します。

## 5. 利用環境・モード分析

### 5.1 `時間帯別リクエスト数`

| 項目 | 内容 |
| --- | --- |
| 表示 | 00:00 から 23:59 までの時間帯別折れ線。 |
| 計算式 | 選択期間内 request を日本時間の hour bucket に累計する。 |
| 期間変更時 | 選択日数が増えても x 軸は24時間固定。 |

### 5.2 `利用デバイス分布`

| 表示名 | Raw value |
| --- | --- |
| `PC` | `desktop` |
| `モバイル` | `mobile` |
| `不明` | `unknown` |

計算式: `device_class` ごとの request または ask activity 比率。

### 5.3 `利用モード分布`

| 表示名 | Raw value |
| --- | --- |
| `社内モード` | `internal` |
| `Web検索モード` | `websearch` |
| `その他` | その他 mode |

MVP では `internal` と `websearch` を主要対象にします。

## 6. 回答品質分析

| 表示名 | Source | 集計 |
| --- | --- | --- |
| `回答可能性` | `answerability_level` | 値ごとの件数と割合 |
| `回答利用可能性` | `usability_level` | 値ごとの件数と割合 |
| `業務利用可能性` | `delivery_readiness` | 値ごとの件数と割合 |
| `根拠十分性` | `evidence_sufficiency` | 値ごとの件数と割合 |

推奨表示は stacked bar または donut とし、technical enum は管理者向けラベルに変換します。

## 7. 追問分析

| 表示名 | 計算式 |
| --- | --- |
| `追問認識数` | recognized follow-up event 数。 |
| `追問成功率` | `success_count / recognized_count`。 |
| `明示的な訂正` | correction 判定の follow-up turn 数。 |
| `確認が必要な追問` | clarification required の follow-up turn 数。 |

`fallback_reason`、`state_action`、`reason_signals`、`followup_offtopic` は export / BigQuery で保持し、画面では必要に応じて business label に集約します。

## 8. ユーザー監視一覧

| 表示カラム | 計算式 / source |
| --- | --- |
| `ユーザーID` | `user_id` |
| `メールアドレス` | `user_email` |
| `最終利用日時` | 最新 message / ask activity の日本時間。 |
| `直近7日利用日数` | 直近7日で message または ask activity がある日数。 |
| `直近7日メッセージ数` | 直近7日の user message 数。 |
| `根拠カバレッジ率` | `1 - low_coverage_rate`。 |
| `低評価率` | `bad_feedback_count / feedback_count`。 |
| `活性度区分` | 14日活性度定義。 |

## 9. ユーザー詳細

| セクション | 主な指標 |
| --- | --- |
| `ユーザーサマリー` | message 数、回答成功率、低カバレッジ率、低評価率、追問数 |
| `利用推移` | 日別 message 数、回答成功率、低カバレッジ率、feedback |
| `利用モード分布` | `社内モード`, `Web検索モード`, `その他` |
| `回答品質分布` | 回答可能性、回答利用可能性、業務利用可能性、根拠十分性 |
| `追問状況` | 追問認識、追問成功率、訂正、確認要求 |
| `会話一覧` | conversation-level records |

## 10. チャット記録確認タグ

| タグ | 条件 |
| --- | --- |
| `回答成功` | `回答成功率` の成功条件を満たす assistant answer。 |
| `低カバレッジ` | 低カバレッジ条件に該当する。 |
| `低評価` | bad feedback が存在する。 |
| `追問` | follow-up turn として認識されている。 |
| `エラー` | request または message が error。 |
| `要確認` | clarification required、低 delivery readiness、schema/join issue のいずれか。 |

## 11. データ健全性

| 表示名 | 計算式 |
| --- | --- |
| `イベント件数` | event family / schema version 別件数。 |
| `必須項目欠落` | required identity fields の欠落件数。 |
| `Schema不一致` | 未対応 schema version または schema validation error 件数。 |
| `Join健全性` | expected join の成功率。 |
| `データ遅延` | `monitor_available_at - event_ts` の P95。 |
