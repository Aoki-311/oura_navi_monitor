from pathlib import Path


def test_runbook_has_complete_promotion_lock_and_provenance_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "THREE_HOUR_RECOVERY_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    for required in (
        '--expected-build-id "<EXACT_CLOUD_BUILD_ID>"',
        '--expected-job-service-account "<EXACT_REFRESH_JOB_SA>"',
        '--legacy-transfer-resource "<EXACT_LEGACY_DTS_TRANSFER_RESOURCE>"',
        '--firestore-database "lcs-user-data"',
        '--release-lock-collection "monitor_release_locks"',
        "roles/datastore.user",
        "read/create/set/delete",
        "没有自动过期",
        "完全相同的参数和同一个 `--snapshot-output` 重跑",
        "response.metadata.name",
        "response.response.name",
        "不能用时间邻近",
        "plan 只渲染本地参数",
        '--intent-disposition "<aborted_pre|authorized_post_recovery>"',
        "--allow-intent-release",
        "--confirm-intent-release",
        "aborted_pre` 永久拒绝同一个旧 intent",
        "authorized_post_recovery",
        "绝不能进入 `update-traffic`",
    ):
        assert required in text

    assert "Cloud Run execution 创建时间关联" not in text
