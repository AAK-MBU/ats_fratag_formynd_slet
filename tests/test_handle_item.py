"""Tests af behandlingen af sletteprocessens items."""

import pytest
from mbu_rpa_core.exceptions import BusinessError

from ats_framework.helpers.formynd_db import DeleteRefused
from ats_framework.processes import handle_item, slet_config


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(
        handle_item, "send_report_email", lambda s, h: sent.append((s, h))
    )
    return sent


def _delete_returning(monkeypatch, outcome):
    def fake(sag_id, frist):
        assert frist == slet_config.frist()
        if isinstance(outcome, Exception):
            raise outcome
        return {**outcome, "sagId": sag_id}

    monkeypatch.setattr(handle_item, "delete_sag", fake)


def test_slet_sag_ok(monkeypatch, mails):
    _delete_returning(
        monkeypatch,
        {
            "status": "slettet",
            "raekker": {"ProcesEventLog": 4, "Sag": 1},
            "sletningId": 9,
        },
    )
    message = handle_item.handle_item({"type": "slet_sag", "sagId": 7}, "slet_sag_7")
    assert message == (
        "Sag 7: slettet, 5 databaserækker (ProcesEventLog=4, Sag=1), sletning 9"
    )
    assert mails == []


def test_slet_sag_already_deleted(monkeypatch):
    _delete_returning(
        monkeypatch, {"status": "allerede_slettet", "raekker": {}, "sletningId": 9}
    )
    message = handle_item.handle_item({"type": "slet_sag", "sagId": 7}, "slet_sag_7")
    assert message == "Sag 7: allerede_slettet, 0 databaserækker (-), sletning 9"


def test_slet_sag_refused_is_business_error(monkeypatch, mails):
    _delete_returning(monkeypatch, DeleteRefused("Sag 7 er stadig aktiv"))
    with pytest.raises(BusinessError, match="stadig aktiv"):
        handle_item.handle_item({"type": "slet_sag", "sagId": 7}, "slet_sag_7")
    assert mails == []


@pytest.mark.usefixtures("mails")
def test_slet_sag_db_error_propagates(monkeypatch):
    _delete_returning(monkeypatch, RuntimeError("db nede"))
    with pytest.raises(RuntimeError):
        handle_item.handle_item({"type": "slet_sag", "sagId": 7}, "slet_sag_7")


def test_dry_run_mails_candidates_and_unclosed(mails):
    data = {
        "type": "dry_run",
        "dato": "2026-10-08",
        "dage": 30,
        "frist": "2026-09-08",
        "kandidater": [{"sagId": 12, "sagType": "FRATAG", "slutDato": "2026-08-01"}],
        "ikkeLukket": [{"sagId": 3, "sagType": "FRATAG", "slutDato": "2026-07-01"}],
    }
    message = handle_item.handle_item(data, "dryrun_2026-10-08")
    assert message == "Dry-run: 1 kandidater, 1 ikke lukket"
    subject, html = mails[0]
    assert subject.startswith(slet_config.SUBJECT_PREFIX)
    assert "1 sager ville blive slettet" in subject
    assert "<td>12</td>" in html and "<td>3</td>" in html


def test_ikke_lukket_mails_list(mails):
    data = {"type": "ikke_lukket", "sager": [{"sagId": 3, "sagType": "<x>"}]}
    handle_item.handle_item(data, "ikke_lukket_2026-10-08")
    subject, html = mails[0]
    assert "1 udløbne sager" in subject
    assert "&lt;x&gt;" in html


@pytest.mark.usefixtures("mails")
def test_unknown_type_is_business_error():
    with pytest.raises(BusinessError):
        handle_item.handle_item({"type": "nope"}, "ref")
