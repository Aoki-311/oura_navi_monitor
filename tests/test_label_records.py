from app.domain.label_records import (
    read_canonical_label,
    read_canonical_label_collection,
)
from app.domain.roster_records import DOCUMENT_ID_FIELD


def _valid_label() -> dict:
    return {
        "label_id": "label_1",
        "name": "重点",
        "color": "#23d28f",
        "is_active": True,
        "usage_count": 1,
    }


def test_inactive_label_is_a_valid_catalog_record() -> None:
    record = read_canonical_label({**_valid_label(), "is_active": False})

    assert record.catalog_eligible is True
    assert record.value["is_active"] is False
    assert record.issues == ()


def test_label_reader_fails_closed_for_truthy_string_activity() -> None:
    record = read_canonical_label({**_valid_label(), "is_active": "false"})

    assert record.value["is_active"] is False
    assert record.issues == ("invalid_label_is_active",)
    assert record.catalog_eligible is False


def test_label_document_id_is_preserved_as_repair_address_but_mismatch_is_blocked() -> None:
    record = read_canonical_label(
        {
            **_valid_label(),
            DOCUMENT_ID_FIELD: "firestore_label_doc",
            "label_id": "wrong_label_id",
        }
    )

    assert record.value["label_id"] == "firestore_label_doc"
    assert record.issues == ("label_id_document_mismatch",)
    assert record.catalog_eligible is False


def test_non_mapping_label_row_is_a_diagnostic_not_an_exception() -> None:
    record = read_canonical_label("broken-label")

    assert "invalid_label_record" in record.issues
    assert record.catalog_eligible is False


def test_collection_reader_marks_every_duplicate_label_id_row() -> None:
    records = read_canonical_label_collection(
        [
            _valid_label(),
            {**_valid_label(), "name": "別名", "color": "#386dff"},
        ]
    )

    assert [record.issues for record in records] == [
        ("duplicate_label_id",),
        ("duplicate_label_id",),
    ]
    assert all(record.catalog_eligible is False for record in records)


def test_collection_reader_marks_normalized_duplicate_names_with_different_ids() -> None:
    records = read_canonical_label_collection(
        [
            {**_valid_label(), "name": " ＴＥＳＴ ", DOCUMENT_ID_FIELD: "label_1"},
            {
                **_valid_label(),
                "label_id": "label_2",
                "name": "test",
                "color": "#386dff",
                DOCUMENT_ID_FIELD: "label_2",
            },
            {
                **_valid_label(),
                "label_id": "label_3",
                "name": "別名",
                "color": "#ffb340",
                DOCUMENT_ID_FIELD: "label_3",
            },
        ]
    )

    assert records[0].issues == ("duplicate_label_name",)
    assert records[1].issues == ("duplicate_label_name",)
    assert records[0].catalog_eligible is False
    assert records[1].catalog_eligible is False
    assert records[2].issues == ()
    assert records[2].catalog_eligible is True


def test_same_id_and_name_is_only_an_id_conflict() -> None:
    records = read_canonical_label_collection([_valid_label(), _valid_label()])

    assert [record.issues for record in records] == [
        ("duplicate_label_id",),
        ("duplicate_label_id",),
    ]


def test_collection_conflict_preserves_document_id_repair_address() -> None:
    records = read_canonical_label_collection(
        [
            {
                **_valid_label(),
                DOCUMENT_ID_FIELD: "firestore_label_doc",
                "label_id": "wrong_label_id",
            },
            {
                **_valid_label(),
                DOCUMENT_ID_FIELD: "label_2",
                "label_id": "label_2",
                "color": "#386dff",
            },
        ]
    )

    assert records[0].document_id == "firestore_label_doc"
    assert records[0].value["label_id"] == "firestore_label_doc"
    assert records[0].issues == (
        "label_id_document_mismatch",
        "duplicate_label_name",
    )
    assert records[0].catalog_eligible is False
    assert records[1].issues == ("duplicate_label_name",)
    assert records[1].catalog_eligible is False
