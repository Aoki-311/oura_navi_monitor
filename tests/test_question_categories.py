import pytest

from app.domain.question_categories import (
    QuestionCategory,
    migrate_legacy_question_category,
    parse_question_category,
)


EXPECTED_KEYS = {
    "product_information",
    "price_product_code",
    "comparison_fit_selection",
    "usage_procedure",
    "troubleshooting_safety",
    "sales_proposal",
    "institution_gpo_market",
    "document_search",
    "other_general",
    "unclassified",
}


def test_question_category_contract_is_closed() -> None:
    assert {item.value for item in QuestionCategory} == EXPECTED_KEYS


def test_unknown_category_is_rejected_instead_of_keyword_or_default_mapping() -> None:
    with pytest.raises(ValueError):
        parse_question_category("topic_ideation")
    with pytest.raises(ValueError):
        parse_question_category("")


def test_explicit_unclassified_is_valid() -> None:
    assert parse_question_category("unclassified") is QuestionCategory.UNCLASSIFIED


def test_retired_category_migration_is_exact_and_does_not_reopen_runtime_contract() -> None:
    assert (
        migrate_legacy_question_category("product_explanation")
        is QuestionCategory.PRODUCT_INFORMATION
    )
    assert (
        migrate_legacy_question_category("topic_ideation")
        is QuestionCategory.UNCLASSIFIED
    )
    assert migrate_legacy_question_category("product explanation") is QuestionCategory.UNCLASSIFIED
    with pytest.raises(ValueError):
        parse_question_category("product_explanation")
