from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_research_config_is_read_only():
    config = yaml.safe_load((ROOT / "config" / "research.yaml").read_text())

    assert config["project"]["mcp_read_only"] is True
    assert config["data_policy"]["full_period_tick_download_allowed"] is False
    assert config["data_policy"]["tick_data_policy"] == "finalists_only"


def test_final_split_is_locked():
    splits = yaml.safe_load((ROOT / "config" / "data_splits.yaml").read_text())

    assert splits["splits"]["final_untouched_test"]["locked"] is True

