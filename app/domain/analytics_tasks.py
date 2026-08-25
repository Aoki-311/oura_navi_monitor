from __future__ import annotations

from enum import StrEnum


class AnalyticsTask(StrEnum):
    FACT_LOOKUP = "fact_lookup"
    EXPLANATION = "explanation"
    COMPARISON_SELECTION = "comparison_selection"
    PROCEDURE_GUIDANCE = "procedure_guidance"
    TROUBLESHOOTING = "troubleshooting"
    CONTENT_CREATION = "content_creation"
    SOURCE_RETRIEVAL = "source_retrieval"
    MARKET_RESEARCH = "market_research"
    OTHER = "other"
    UNCLASSIFIED = "unclassified"


ANALYTICS_TASK_LABELS: dict[AnalyticsTask, str] = {
    AnalyticsTask.FACT_LOOKUP: "情報確認",
    AnalyticsTask.EXPLANATION: "説明依頼",
    AnalyticsTask.COMPARISON_SELECTION: "比較・選定",
    AnalyticsTask.PROCEDURE_GUIDANCE: "手順確認",
    AnalyticsTask.TROUBLESHOOTING: "問題解決",
    AnalyticsTask.CONTENT_CREATION: "資料・文面作成",
    AnalyticsTask.SOURCE_RETRIEVAL: "資料検索",
    AnalyticsTask.MARKET_RESEARCH: "市場・施設調査",
    AnalyticsTask.OTHER: "その他",
    AnalyticsTask.UNCLASSIFIED: "判定不能",
}


def analytics_task(value: object) -> AnalyticsTask:
    try:
        return AnalyticsTask(str(value or "").strip())
    except ValueError:
        return AnalyticsTask.UNCLASSIFIED


def analytics_task_label(value: AnalyticsTask | str) -> str:
    task = value if isinstance(value, AnalyticsTask) else analytics_task(value)
    return ANALYTICS_TASK_LABELS[task]
