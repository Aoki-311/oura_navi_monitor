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


def parse_question_category(value: str) -> QuestionCategory:
    """Validate a producer value without guessing from text or aliases."""

    return QuestionCategory(str(value or "").strip())
