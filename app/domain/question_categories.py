from __future__ import annotations

from enum import StrEnum


class QuestionCategory(StrEnum):
    PRODUCT_INFORMATION = "product_information"
    PRICE_PRODUCT_CODE = "price_product_code"
    COMPARISON_FIT_SELECTION = "comparison_fit_selection"
    USAGE_PROCEDURE = "usage_procedure"
    TROUBLESHOOTING_SAFETY = "troubleshooting_safety"
    SALES_PROPOSAL = "sales_proposal"
    INSTITUTION_GPO_MARKET = "institution_gpo_market"
    DOCUMENT_SEARCH = "document_search"
    OTHER_GENERAL = "other_general"
    UNCLASSIFIED = "unclassified"


QUESTION_CATEGORY_LABELS: dict[QuestionCategory, str] = {
    QuestionCategory.PRODUCT_INFORMATION: "製品情報・仕様",
    QuestionCategory.PRICE_PRODUCT_CODE: "価格・製品コード",
    QuestionCategory.COMPARISON_FIT_SELECTION: "比較・適合・選定",
    QuestionCategory.USAGE_PROCEDURE: "使用方法・手順",
    QuestionCategory.TROUBLESHOOTING_SAFETY: "トラブル・安全対応",
    QuestionCategory.SALES_PROPOSAL: "営業活動・提案作成",
    QuestionCategory.INSTITUTION_GPO_MARKET: "医療機関・GPO・市場情報",
    QuestionCategory.DOCUMENT_SEARCH: "資料・文書を探す",
    QuestionCategory.OTHER_GENERAL: "その他・一般質問",
    QuestionCategory.UNCLASSIFIED: "判定不能",
}


# One-time schema migration only.  Runtime producer admission remains strict and
# never accepts these retired values.  ``topic_ideation`` was the old catch-all
# default, so it cannot honestly be promoted to a more specific category.
LEGACY_QUESTION_CATEGORY_MIGRATION: dict[str, QuestionCategory] = {
    "product_explanation": QuestionCategory.PRODUCT_INFORMATION,
    "product_price": QuestionCategory.PRICE_PRODUCT_CODE,
    "troubleshooting": QuestionCategory.TROUBLESHOOTING_SAFETY,
    "sales_approach": QuestionCategory.SALES_PROPOSAL,
    "hospital_gpo": QuestionCategory.INSTITUTION_GPO_MARKET,
    "topic_ideation": QuestionCategory.UNCLASSIFIED,
}


def parse_question_category(value: str) -> QuestionCategory:
    """Validate a producer value without guessing from text or aliases."""

    return QuestionCategory(str(value or "").strip())


def migrate_legacy_question_category(value: object) -> QuestionCategory:
    """Map only an exact retired enum during the one-time history rebuild."""

    return LEGACY_QUESTION_CATEGORY_MIGRATION.get(
        str(value or "").strip(), QuestionCategory.UNCLASSIFIED
    )


def analytics_question_category(value: object) -> QuestionCategory:
    """Project missing/legacy-invalid analytics values to an explicit bucket.

    Producer admission remains strict through ``parse_question_category`` and the
    BigQuery quality gate.  The read model must not make unrelated analytics
    disappear when a historical row was never classified.
    """

    try:
        return parse_question_category(str(value or ""))
    except ValueError:
        return QuestionCategory.UNCLASSIFIED


def question_category_label(value: QuestionCategory | str) -> str:
    category = (
        value if isinstance(value, QuestionCategory) else analytics_question_category(value)
    )
    return QUESTION_CATEGORY_LABELS[category]
