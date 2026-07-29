from src.phase6_v2_artifact import build_phase6_v2_artifact


def test_phase6_v2_artifact_is_bounded_complete_and_source_backed():
    artifact = build_phase6_v2_artifact()
    manifest = artifact["manifest"]
    snapshot = artifact["snapshot"]

    assert artifact["surface"] == "report"
    assert manifest["blocks"][0]["body"] == f"# {manifest['title']}"
    assert len(manifest["charts"]) == 2
    assert len(manifest["tables"]) == 1
    assert snapshot["status"] == "ready"
    assert set(snapshot["datasets"]) == {
        "headline",
        "range_by_state",
        "annual_stability",
        "ledger_table",
    }
    assert len(snapshot["datasets"]["range_by_state"]) == 4
    assert len(snapshot["datasets"]["annual_stability"]) == 8
    assert len(snapshot["datasets"]["ledger_table"]) == 4
    source_ids = {source["id"] for source in manifest["sources"]}
    assert all(
        item["sourceId"] in source_ids
        for item in manifest["cards"]
        + manifest["charts"]
        + manifest["tables"]
    )
