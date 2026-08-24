from app.domain.analysis_scopes import AnalysisScope, Department, membership_for


def test_department_is_the_only_scope_owner() -> None:
    assert membership_for(Department.DM_FIELD, is_active=True).includes(AnalysisScope.GLOBAL)
    assert membership_for(Department.HEALTHCARE_HQ, is_active=True).includes(AnalysisScope.GLOBAL)
    assert not membership_for(Department.DM_HQ, is_active=True).includes(AnalysisScope.GLOBAL)
    assert membership_for(Department.DM_HQ, is_active=True).includes(AnalysisScope.USER_MAP)
    assert not membership_for(Department.ADMIN, is_active=True).includes(AnalysisScope.USER_MAP)
    assert not membership_for(Department.ADMIN, is_active=True).includes(AnalysisScope.USER_MAP)


def test_inactive_user_is_excluded_from_every_analysis_scope() -> None:
    membership = membership_for(Department.DM_FIELD, is_active=False)
    assert not membership.includes(AnalysisScope.GLOBAL)
    assert not membership.includes(AnalysisScope.USER_MAP)


def test_initial_roster_shape_produces_69_80_83_without_count_constants() -> None:
    departments = (
        [Department.DM_FIELD] * 61
        + [Department.HEALTHCARE_HQ] * 8
        + [Department.DM_HQ] * 11
        + [Department.ADMIN] * 3
    )
    memberships = [membership_for(value, is_active=True) for value in departments]
    assert sum(item.includes(AnalysisScope.GLOBAL) for item in memberships) == 69
    assert sum(item.includes(AnalysisScope.USER_MAP) for item in memberships) == 80
    assert len(memberships) == 83


def test_monitor_labels_never_change_scope_membership() -> None:
    baseline = membership_for(Department.DM_HQ, is_active=True)
    with_labels = membership_for(Department.DM_HQ, is_active=True, label_ids=["重点", "研修対象"])
    assert with_labels == baseline
