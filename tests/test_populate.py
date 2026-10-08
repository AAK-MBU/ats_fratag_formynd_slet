"""Tests af opbygningen af kø-items ud fra de udløbne sager."""

from datetime import date
from types import SimpleNamespace

from ats_framework.processes import populate, slet_config

CANDIDATES = {
    "kandidater": [
        {"sagId": 12, "sagType": "FRATAG", "slutDato": "2026-08-01"},
        {"sagId": 13, "sagType": "FRATAG", "slutDato": "2026-08-02"},
    ],
    "ikkeLukket": [{"sagId": 3, "sagType": "FRATAG", "slutDato": "2026-07-01"}],
}


def test_dry_run_builds_one_report_item():
    items = populate.build_items(CANDIDATES, "2026-10-08", "2026-09-08", True)
    assert [i["reference"] for i in items] == ["dryrun_2026-10-08"]
    data = items[0]["data"]
    assert data["type"] == "dry_run"
    assert data["dage"] == slet_config.SLETTE_DAGE
    assert data["frist"] == "2026-09-08"
    assert [k["sagId"] for k in data["kandidater"]] == [12, 13]
    assert data["ikkeLukket"] == CANDIDATES["ikkeLukket"]


def test_dry_run_with_nothing_builds_nothing():
    assert populate.build_items({}, "2026-10-08", "2026-09-08", True) == []


def test_live_builds_one_item_per_sag_and_a_report():
    items = populate.build_items(CANDIDATES, "2026-10-08", "2026-09-08", False)
    assert [i["reference"] for i in items] == [
        "slet_sag_12",
        "slet_sag_13",
        "ikke_lukket_2026-10-08",
    ]
    assert items[0]["data"] == {
        "type": "slet_sag",
        "sagId": 12,
        "sagType": "FRATAG",
        "slutDato": "2026-08-01",
    }
    assert items[2]["data"]["sager"] == CANDIDATES["ikkeLukket"]


def test_live_without_unclosed_sager_has_no_report():
    candidates = {**CANDIDATES, "ikkeLukket": []}
    items = populate.build_items(candidates, "2026-10-08", "2026-09-08", False)
    assert [i["reference"] for i in items] == ["slet_sag_12", "slet_sag_13"]


def test_collect_items_uses_frist_and_mode(monkeypatch):
    monkeypatch.setattr(slet_config, "DRY_RUN", False)
    seen = {}

    def fake_fetch(frist):
        seen["frist"] = frist
        return CANDIDATES

    monkeypatch.setattr(populate, "fetch_candidates", fake_fetch)
    items = populate.collect_items(SimpleNamespace(name="formynd.slet"))
    assert seen["frist"] == slet_config.frist()
    assert items[0]["reference"] == "slet_sag_12"


def test_frist_is_slette_dage_back():
    assert slet_config.frist(date(2026, 10, 8)) == date(2026, 9, 8)


def test_dry_run_is_default():
    assert slet_config.DRY_RUN is True
