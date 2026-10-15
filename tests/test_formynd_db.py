"""Tests af læsning og sletning af sager i formynd mod en in-memory SQLite-DB.

Modellerne ligger i skemaerne ``formynd`` og ``rpa``, så der ATTACHes en
``:memory:``-database pr. skemanavn. ``SYSUTCDATETIME()`` (brugt af
server_defaults) registreres som SQL-funktion, og BigInteger renderes som
INTEGER, da SQLite kun auto-inkrementerer en INTEGER PRIMARY KEY.
``models._session_factory`` overskrives, så den rigtige ``db_session`` bruger
SQLite-motoren.
"""

import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import BigInteger, create_engine, event, func, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ats_framework.helpers import formynd_db, models, rpa_db
from ats_framework.helpers.models import (
    Constant,
    ProcesEventLog,
    Sag,
    SagESDHReference,
    SagNotifikation,
    SagPartSvar,
)

FRIST = date(2026, 9, 8)
NOW = datetime(2026, 10, 8, 2, 0, tzinfo=UTC)


@compiles(BigInteger, "sqlite")
def _bigint_as_integer_sqlite(_type, _compiler, **_kw):
    return "INTEGER"


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _setup(dbapi_conn, _record):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS formynd")
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS rpa")
        dbapi_conn.create_function("SYSUTCDATETIME", 0, lambda: NOW.isoformat(sep=" "))

    models.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        models,
        "_session_factory",
        lambda: sessionmaker(bind=engine, expire_on_commit=False),
    )
    return engine


def _add_sag(engine, *, slut: date, aktiv: bool, sag_type: str = "FRATAG") -> int:
    with Session(engine) as s:
        sag = Sag(
            SagType=sag_type,
            BarnCPR="0101150000",
            Foraeldre1CPR="0101800000",
            StartDato=date(2025, 1, 1),
            SlutDato=slut,
            ErAktiv=aktiv,
        )
        s.add(sag)
        s.commit()
        return sag.SagId


def _add_children(engine, sag_id: int) -> None:
    with Session(engine) as s:
        s.add_all(
            [
                SagESDHReference(SagId=sag_id, FormId="F1", ESDHId="E1"),
                *(
                    SagNotifikation(
                        SagId=sag_id,
                        NotifikationType=t,
                        SlutDato=date(2026, 8, 1),
                        NotificeretDato=NOW,
                        SourceComponent="x",
                    )
                    for t in ("AABN", "LUK")
                ),
                SagPartSvar(
                    SagId=sag_id,
                    NotifikationType="AABN",
                    SlutDato=date(2026, 8, 1),
                    Svar="UDFOERT",
                    FormSubmissionId="S1",
                ),
                ProcesEventLog(
                    SagId=sag_id,
                    EventType="NOTIFIKATION",
                    EventAction="AABN",
                    SourceSystem="PYTHON",
                    EventPayload='{"mail": "0101150000"}',
                ),
            ]
        )
        s.commit()


def _count(engine, model, sag_id: int) -> int:
    with Session(engine) as s:
        return s.scalar(
            select(func.count()).select_from(model).where(model.SagId == sag_id)
        )


def test_fetch_candidates_splits_closed_and_active(engine):
    lukket = _add_sag(engine, slut=date(2026, 1, 1), aktiv=False)
    aktiv = _add_sag(engine, slut=date(2026, 2, 1), aktiv=True)
    _add_sag(engine, slut=FRIST, aktiv=False)  # ikke før frist

    result = formynd_db.fetch_candidates(FRIST)

    assert result == {
        "kandidater": [
            {"sagId": lukket, "sagType": "FRATAG", "slutDato": "2026-01-01"}
        ],
        "ikkeLukket": [{"sagId": aktiv, "sagType": "FRATAG", "slutDato": "2026-02-01"}],
    }


def test_delete_removes_children_then_sag_and_logs(engine):
    sag_id = _add_sag(engine, slut=date(2026, 8, 1), aktiv=False)
    other = _add_sag(engine, slut=date(2026, 8, 1), aktiv=False)
    _add_children(engine, sag_id)
    _add_children(engine, other)

    result = formynd_db.delete_sag(sag_id, FRIST)

    assert result["status"] == "slettet"
    assert list(result["raekker"]) == [
        *(m.__tablename__ for m in formynd_db.CHILD_TABLES),
        "Sag",
    ]
    assert result["raekker"] == {
        "SagPartSvar": 1,
        "SagNotifikation": 2,
        "SagESDHReference": 1,
        "ProcesEventLog": 1,
        "Sag": 1,
    }
    for model in (SagPartSvar, SagESDHReference, Sag):
        assert _count(engine, model, sag_id) == 0
        assert _count(engine, model, other) == 1
    assert _count(engine, SagNotifikation, sag_id) == 0
    assert _count(engine, SagNotifikation, other) == 2

    with Session(engine) as s:
        log = s.scalars(
            select(ProcesEventLog).where(ProcesEventLog.SagId == sag_id)
        ).one()
    assert log.EventId == result["sletningId"]
    assert log.EventType == formynd_db.SLETTELOG_EVENT_TYPE
    payload = json.loads(log.EventPayload)
    assert payload["slutDato"] == "2026-08-01"
    assert "CPR" not in log.EventPayload
    assert "0101150000" not in log.EventPayload
    assert _count(engine, ProcesEventLog, other) == 1


@pytest.mark.parametrize(
    ("slut", "aktiv", "reason"),
    [
        (date(2026, 8, 1), True, "aktiv"),
        (FRIST, False, "ikke er før"),
    ],
)
def test_delete_refuses_when_rule_not_met(engine, slut, aktiv, reason):
    sag_id = _add_sag(engine, slut=slut, aktiv=aktiv)
    _add_children(engine, sag_id)

    with pytest.raises(formynd_db.DeleteRefused, match=reason):
        formynd_db.delete_sag(sag_id, FRIST)

    assert _count(engine, Sag, sag_id) == 1
    assert _count(engine, SagNotifikation, sag_id) == 2


def test_delete_twice_is_already_deleted(engine):
    sag_id = _add_sag(engine, slut=date(2026, 8, 1), aktiv=False)
    first = formynd_db.delete_sag(sag_id, FRIST)

    second = formynd_db.delete_sag(sag_id, FRIST)

    assert second == {
        "sagId": sag_id,
        "status": "allerede_slettet",
        "raekker": {},
        "sletningId": first["sletningId"],
    }


@pytest.mark.usefixtures("engine")
def test_delete_unknown_sag_is_refused():
    with pytest.raises(formynd_db.DeleteRefused, match="findes ikke"):
        formynd_db.delete_sag(7, FRIST)


def test_delete_rolls_back_when_a_delete_fails(engine):
    sag_id = _add_sag(engine, slut=date(2026, 8, 1), aktiv=False)
    _add_children(engine, sag_id)

    @event.listens_for(engine, "before_cursor_execute")
    def _fail_on_sag_delete(_conn, _cursor, statement, *_args):
        if statement.startswith('DELETE FROM formynd."Sag"'):
            raise RuntimeError("FK violation")

    with pytest.raises(RuntimeError, match="FK"):
        formynd_db.delete_sag(sag_id, FRIST)
    event.remove(engine, "before_cursor_execute", _fail_on_sag_delete)

    assert _count(engine, Sag, sag_id) == 1
    assert _count(engine, SagNotifikation, sag_id) == 2
    assert _count(engine, ProcesEventLog, sag_id) == 1


def test_get_constant(engine):
    with Session(engine) as s:
        s.add(Constant(name="smtp_port", value="25"))
        s.commit()

    assert rpa_db.get_constant("smtp_port") == "25"
    with pytest.raises(LookupError):
        rpa_db.get_constant("findes_ikke")
