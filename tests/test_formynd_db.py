"""Tests af læsning og sletning af sager i formynd mod en fake cursor."""

import json
from contextlib import contextmanager
from datetime import date

import pytest

from ats_framework.helpers import formynd_db

FRIST = date(2026, 9, 8)


class FakeCursor:
    """Optager SQL-kald og svarer med på forhånd angivne resultater.

    ``results`` er en liste af ``(columns, rows)`` som SELECT/OUTPUT-kald
    får i rækkefølge. DELETE-kald får ``rowcounts[tabel]``.
    """

    def __init__(self, results=(), rowcounts=None):
        self.results = list(results)
        self.rowcounts = rowcounts or {}
        self.calls = []
        self.description = None
        self._rows = []
        self.rowcount = -1

    def execute(self, sql, *params):
        self.calls.append((sql, params))
        if sql.startswith("DELETE"):
            table = sql.split("formynd.")[1].split()[0]
            self.rowcount = self.rowcounts.get(table, 0)
            return self
        columns, self._rows = self.results.pop(0)
        self.description = [(c,) for c in columns]
        return self

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def db(monkeypatch):
    state = {"committed": False}

    def install(cursor):
        @contextmanager
        def fake_transaction():
            yield cursor
            state["committed"] = True

        monkeypatch.setattr(formynd_db, "_transaction", fake_transaction)
        return state

    return install


SAG_COLS = ["SagType", "SlutDato", "ErAktiv"]


def test_fetch_candidates_splits_closed_and_active(db):
    cursor = FakeCursor(
        [
            (
                ["SagId", "SagType", "SlutDato", "ErAktiv"],
                [
                    (1, "FRATAG", date(2026, 1, 1), False),
                    (2, "FRATAG", date(2026, 2, 1), True),
                ],
            )
        ]
    )
    db(cursor)
    result = formynd_db.fetch_candidates(FRIST)
    assert result == {
        "kandidater": [{"sagId": 1, "sagType": "FRATAG", "slutDato": "2026-01-01"}],
        "ikkeLukket": [{"sagId": 2, "sagType": "FRATAG", "slutDato": "2026-02-01"}],
    }
    assert cursor.calls[0][1] == (FRIST,)


def test_delete_removes_children_then_sag_and_logs(db):
    cursor = FakeCursor(
        [
            (SAG_COLS, [("FRATAG", date(2026, 8, 1), False)]),
            (["EventId"], [(99,)]),
        ],
        rowcounts={"SagNotifikation": 3, "ProcesEventLog": 5, "Sag": 1},
    )
    state = db(cursor)
    result = formynd_db.delete_sag(7, FRIST)

    assert result["status"] == "slettet"
    assert result["sletningId"] == 99
    assert result["raekker"]["Sag"] == 1
    assert sum(result["raekker"].values()) == 9
    deletes = [sql for sql, _ in cursor.calls if sql.startswith("DELETE")]
    assert [d.split("formynd.")[1].split()[0] for d in deletes] == [
        *formynd_db.CHILD_TABLES,
        "Sag",
    ]
    insert_sql, insert_params = cursor.calls[-1]
    assert "INSERT INTO formynd.ProcesEventLog" in insert_sql
    payload = json.loads(insert_params[4])
    assert payload["slutDato"] == "2026-08-01"
    assert "CPR" not in insert_params[4]
    assert state["committed"]


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (("FRATAG", date(2026, 8, 1), True), "aktiv"),
        (("FRATAG", FRIST, False), "ikke er før"),
    ],
)
def test_delete_refuses_when_rule_not_met(db, row, reason):
    cursor = FakeCursor([(SAG_COLS, [row])])
    state = db(cursor)
    with pytest.raises(formynd_db.DeleteRefused, match=reason):
        formynd_db.delete_sag(7, FRIST)
    assert not any(sql.startswith("DELETE") for sql, _ in cursor.calls)
    assert not state["committed"]


def test_delete_missing_sag_with_slettelog_is_already_deleted(db):
    cursor = FakeCursor([(SAG_COLS, []), (["EventId"], [(42,)])])
    db(cursor)
    result = formynd_db.delete_sag(7, FRIST)
    assert result == {
        "sagId": 7,
        "status": "allerede_slettet",
        "raekker": {},
        "sletningId": 42,
    }


def test_delete_unknown_sag_is_refused(db):
    cursor = FakeCursor([(SAG_COLS, []), (["EventId"], [])])
    db(cursor)
    with pytest.raises(formynd_db.DeleteRefused, match="findes ikke"):
        formynd_db.delete_sag(7, FRIST)


def test_delete_rolls_back_when_a_delete_fails(db):
    class FailingCursor(FakeCursor):
        def execute(self, sql, *params):
            if sql.startswith("DELETE FROM formynd.Sag "):
                raise RuntimeError("FK violation")
            return super().execute(sql, *params)

    cursor = FailingCursor([(SAG_COLS, [("FRATAG", date(2026, 8, 1), False)])])
    state = db(cursor)
    with pytest.raises(RuntimeError, match="FK"):
        formynd_db.delete_sag(7, FRIST)
    assert not state["committed"]
