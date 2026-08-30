from app.domain.roster_records import (
    DOCUMENT_ID_FIELD,
    read_canonical_roster,
    read_canonical_roster_collection,
)


def _valid_row() -> dict:
    return {
        "roster_id": "roster_1",
        "name": "利用者",
        "email": "USER@example.com",
        "area": "関西",
        "area_key": "関西",
        "workplace": "大阪",
        "role": "本社MR",
        "department": "DM専任",
        "label_ids": [],
        "is_active": True,
    }


def test_canonical_roster_reader_normalizes_one_valid_record() -> None:
    record = read_canonical_roster(_valid_row())

    assert record.value["email"] == "user@example.com"
    assert record.issues == ()
    assert record.identity_eligible is True
    assert record.projection_eligible is True
    assert record.analytics_eligible is True


def test_document_id_remains_the_repair_address_for_a_missing_or_mismatched_roster_id() -> None:
    missing = read_canonical_roster(
        {**_valid_row(), DOCUMENT_ID_FIELD: "firestore_doc", "roster_id": ""}
    )
    mismatched = read_canonical_roster(
        {
            **_valid_row(),
            DOCUMENT_ID_FIELD: "firestore_doc",
            "roster_id": "wrong_internal_id",
        }
    )

    assert missing.value["roster_id"] == "firestore_doc"
    assert missing.issues == ("missing_roster_id",)
    assert missing.analytics_eligible is False
    assert mismatched.value["roster_id"] == "firestore_doc"
    assert mismatched.issues == ("roster_id_document_mismatch",)
    assert mismatched.projection_eligible is False


def test_missing_role_is_visible_to_user_map_but_malformed_structure_is_fail_closed() -> None:
    missing_role = read_canonical_roster({**_valid_row(), "role": ""})
    malformed = read_canonical_roster(
        {
            **_valid_row(),
            "email": "not-an-email",
            "label_ids": "not-an-array",
            "is_active": "false",
        }
    )

    assert missing_role.issues == ("missing_role",)
    assert missing_role.analytics_eligible is True
    assert missing_role.evaluation.membership.user_map_enabled is True
    assert missing_role.evaluation.membership.global_enabled is False
    assert malformed.value["label_ids"] == []
    assert malformed.value["is_active"] is False
    assert set(malformed.issues) == {
        "invalid_email",
        "invalid_label_ids",
        "invalid_is_active",
    }
    assert malformed.identity_eligible is False
    assert malformed.analytics_eligible is False


def test_duplicate_label_reference_is_diagnosed_but_does_not_hide_the_user() -> None:
    record = read_canonical_roster(
        {**_valid_row(), "label_ids": ["label_1", "label_1"]}
    )

    assert record.value["label_ids"] == ["label_1"]
    assert record.issues == ("duplicate_label_reference",)
    assert record.analytics_eligible is True


def test_invalid_or_inconsistent_reporting_locations_remain_repairable_but_are_isolated() -> None:
    unsupported = read_canonical_roster(
        {**_valid_row(), "area": "旧エリア", "area_key": "旧エリア"}
    )
    mismatched_key = read_canonical_roster(
        {**_valid_row(), "area": "関西", "area_key": "九州"}
    )
    invalid_headquarters = read_canonical_roster(
        {
            **_valid_row(),
            "area": "本社",
            "area_key": "本社・虎ノ門",
            "workplace": "旧本社",
        }
    )

    assert unsupported.value["area"] == "旧エリア"
    assert "invalid_area" in unsupported.issues
    assert unsupported.analytics_eligible is False
    assert unsupported.projection_eligible is False
    assert "invalid_area_key" in mismatched_key.issues
    assert mismatched_key.analytics_eligible is False
    assert "invalid_headquarters_workplace" in invalid_headquarters.issues
    assert invalid_headquarters.analytics_eligible is False


def test_non_mapping_roster_row_is_a_diagnostic_not_an_exception() -> None:
    record = read_canonical_roster("broken-row")

    assert "invalid_roster_record" in record.issues
    assert record.identity_eligible is False
    assert record.projection_eligible is False


def test_collection_reader_isolates_every_normalized_duplicate_identity_group() -> None:
    rows = [
        {**_valid_row(), "roster_id": "email_a", "email": " Same@Example.com "},
        {**_valid_row(), "roster_id": "email_b", "email": "same@example.COM"},
        {
            **_valid_row(),
            "roster_id": "identity_a",
            "email": "identity-a@example.com",
            "user_id": "shared-subject",
        },
        {
            **_valid_row(),
            "roster_id": "identity_b",
            "email": "identity-b@example.com",
            "user_id": "shared-subject",
        },
        {**_valid_row(), "roster_id": "duplicate_roster", "email": "roster-a@example.com"},
        {**_valid_row(), "roster_id": "duplicate_roster", "email": "roster-b@example.com"},
        {**_valid_row(), "roster_id": "safe", "email": "safe@example.com"},
    ]

    records = read_canonical_roster_collection(rows)

    assert records[0].value["email"] == "same@example.com"
    assert records[1].value["email"] == "same@example.com"
    assert all("duplicate_email" in records[index].issues for index in (0, 1))
    assert all("duplicate_identity" in records[index].issues for index in (2, 3))
    assert all("duplicate_roster_id" in records[index].issues for index in (4, 5))
    assert all(records[index].analytics_eligible is False for index in range(6))
    assert records[6].issues == ()
    assert records[6].analytics_eligible is True


def test_an_invalid_duplicate_cannot_make_its_valid_peer_look_unambiguous() -> None:
    records = read_canonical_roster_collection(
        [
            {**_valid_row(), "roster_id": "safe_shape", "email": "Same@example.com"},
            {
                **_valid_row(),
                "roster_id": "bad_location",
                "email": " same@EXAMPLE.com ",
                "area": "旧エリア",
                "area_key": "旧エリア",
            },
        ]
    )

    assert all("duplicate_email" in record.issues for record in records)
    assert all(record.identity_eligible is False for record in records)
    assert all(record.projection_eligible is False for record in records)
    assert all(record.analytics_eligible is False for record in records)


def test_collection_exposes_valid_rows_and_stable_isolation_diagnostics() -> None:
    records = read_canonical_roster_collection(
        [
            _valid_row(),
            {
                **_valid_row(),
                "roster_id": "broken_location",
                "email": "broken@example.com",
                "area_key": "",
            },
        ]
    )

    assert [record.value["roster_id"] for record in records.analytics_records] == [
        "roster_1"
    ]
    assert [record.value["roster_id"] for record in records.isolated_records] == [
        "broken_location"
    ]
    assert records.diagnostics.isolated_count == 1
    assert dict(records.diagnostics.issue_counts) == {"missing_area_key": 1}
    assert records.diagnostics.issues == ("missing_area_key",)

    repaired = read_canonical_roster_collection([_valid_row()])
    assert repaired.diagnostics.isolated_count == 0
    assert repaired.diagnostics.issue_counts == ()
    assert repaired.diagnostics.fingerprint != records.diagnostics.fingerprint
