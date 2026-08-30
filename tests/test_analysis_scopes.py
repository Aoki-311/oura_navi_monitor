from app.domain.analysis_scopes import (
    AnalysisScope,
    Department,
    SCOPE_POLICY_VERSION,
    SUMMARY_ROLES,
    evaluate_membership,
    membership_for,
)


def membership(role: str, department: Department, *, is_active: bool = True):
    return membership_for(
        role=role,
        department=department,
        is_active=is_active,
    )


def test_summary_scope_is_owned_by_exact_canonical_role() -> None:
    for role in SUMMARY_ROLES:
        result = membership(role, Department.DM_FIELD)
        assert result.includes(AnalysisScope.GLOBAL)
        assert result.includes(AnalysisScope.USER_MAP)

    for role in ("本部メンバー", "ヘルスケア本社", "本社MR候補"):
        result = membership(role, Department.DM_FIELD)
        assert not result.includes(AnalysisScope.GLOBAL)
        assert result.includes(AnalysisScope.USER_MAP)

    assert SCOPE_POLICY_VERSION == "summary_role_v1"


def test_department_only_controls_user_map_and_admin_exclusion() -> None:
    contract = membership("コントラクトMR", Department.DM_HQ)
    assert contract.includes(AnalysisScope.GLOBAL)
    assert contract.includes(AnalysisScope.USER_MAP)

    admin = membership("本社MR", Department.ADMIN)
    assert not admin.includes(AnalysisScope.GLOBAL)
    assert not admin.includes(AnalysisScope.USER_MAP)


def test_inactive_user_is_excluded_from_every_analysis_scope() -> None:
    result = membership("本社MR", Department.DM_FIELD, is_active=False)
    assert not result.includes(AnalysisScope.GLOBAL)
    assert not result.includes(AnalysisScope.USER_MAP)


def test_initial_roster_shape_produces_61_80_83_without_count_constants() -> None:
    rows = (
        [("本社MR", Department.DM_FIELD)] * 39
        + [("コントラクトMR", Department.DM_FIELD)] * 22
        + [("本部メンバー", Department.DM_HQ)] * 19
        + [("本部メンバー", Department.ADMIN)] * 3
    )
    memberships = [membership(role, department) for role, department in rows]
    assert sum(item.includes(AnalysisScope.GLOBAL) for item in memberships) == 61
    assert sum(item.includes(AnalysisScope.USER_MAP) for item in memberships) == 80
    assert len(memberships) == 83


def test_monitor_labels_never_change_scope_membership_even_when_named_like_role() -> None:
    baseline = membership_for(
        role="本部メンバー",
        department=Department.DM_HQ,
        is_active=True,
    )
    with_labels = membership_for(
        role="本部メンバー",
        department=Department.DM_HQ,
        is_active=True,
        label_ids=["本社MR", "コントラクトMR"],
    )
    assert with_labels == baseline
    assert not with_labels.includes(AnalysisScope.GLOBAL)


def test_invalid_roster_values_only_close_the_scope_owned_by_that_field() -> None:
    missing_role = evaluate_membership(
        role="",
        department=Department.DM_FIELD,
        is_active=True,
    )
    assert missing_role.issues == ("missing_role",)
    assert not missing_role.membership.includes(AnalysisScope.GLOBAL)
    assert missing_role.membership.includes(AnalysisScope.USER_MAP)

    invalid_department = evaluate_membership(
        role="本社MR",
        department="未知部門",
        is_active=True,
    )
    assert invalid_department.issues == ("invalid_department",)
    assert not invalid_department.membership.includes(AnalysisScope.GLOBAL)
    assert not invalid_department.membership.includes(AnalysisScope.USER_MAP)
