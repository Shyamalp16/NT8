from src.phase6_artifact import build_phase6_artifact


def test_phase6_report_artifact_is_bounded_and_has_required_report_shape():
    artifact = build_phase6_artifact()
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


def test_phase6_report_retains_all_hypotheses_and_later_split_usage_is_zero():
    artifact = build_phase6_artifact()
    datasets = artifact["snapshot"]["datasets"]
    headline = datasets["headline"][0]

    assert len(datasets["ledger_table"]) == 21
    assert headline["bh_rejections"] == 0
    assert headline["advancing_hypotheses"] == 0
    assert headline["validation_rows_used"] == 0
    assert headline["final_test_rows_used"] == 0
