from src.phase5_artifact import build_phase5_artifact


def test_phase5_report_artifact_is_bounded_and_has_required_report_shape():
    artifact = build_phase5_artifact()
    manifest = artifact["manifest"]
    snapshot = artifact["snapshot"]

    assert artifact["surface"] == "report"
    assert manifest["title"]
    assert manifest["blocks"][0]["type"] == "markdown"
    assert manifest["blocks"][0]["body"] == f"# {manifest['title']}"
    assert manifest["charts"]
    assert any(block["type"] == "chart" for block in manifest["blocks"])
    assert snapshot["status"] == "ready"
    assert len(snapshot["datasets"]) <= 50
    assert all(len(rows) <= 2000 for rows in snapshot["datasets"].values())
    assert sum(len(rows) for rows in snapshot["datasets"].values()) < 2000


def test_phase5_report_artifact_keeps_final_and_validation_usage_at_zero():
    artifact = build_phase5_artifact()
    headline = artifact["snapshot"]["datasets"]["headline"][0]

    assert headline["validation_rows_used"] == 0
    assert headline["final_test_rows_used"] == 0
