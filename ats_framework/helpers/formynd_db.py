"""Læsning og sletning af sager i ``[RPA].[formynd]``.

Går gennem ORM-modellerne og ``db_session`` i ``ats_framework.helpers.models``
og dermed forbindelsesstrengen i miljøvariablen ``DBCONNECTIONSTRINGPROD``.
Kontoen skal have SELECT, INSERT og DELETE på skemaet ``formynd``.

En sag slettes i én transaktion: reglen tjekkes igen med lås på sagen, alle
sagens rækker slettes, og sletningen skrives i ``formynd.ProcesEventLog`` som
en slettelog-række uden personoplysninger. Fejler et trin, rulles det hele
tilbage.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from ats_framework.helpers.models import (
    ProcesEventLog,
    Sag,
    SagESDHReference,
    SagNotifikation,
    SagPartSvar,
    db_session,
)
from ats_framework.processes import slet_config

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Modellerne med sagens rækker ud over formynd.Sag, i sletterækkefølge. De tre
# første har en fremmednøgle til Sag. ProcesEventLog har ingen, men dens
# payloads indeholder de mails, der er sendt om sagen. En ny tabel med
# fremmednøgle til Sag, der ikke står her, får sletningen af Sag til at fejle,
# og så rulles sletningen tilbage.
CHILD_TABLES = (SagPartSvar, SagNotifikation, SagESDHReference, ProcesEventLog)

SLETTELOG_EVENT_TYPE = "SLETNING"
SLETTELOG_EVENT_ACTION = "SAG_SLETTET"

# Låser sagsrækken til transaktionens afslutning, så reglen ikke kan ændre sig
# mellem tjek og sletning. Hintet gælder kun SQL Server.
_LOCK_HINT = "WITH (UPDLOCK, HOLDLOCK)"


class DeleteRefused(Exception):
    """Sagen kan ikke slettes.

    Attributes:
        reason: Hvorfor sagen ikke blev slettet.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _sag(sag_id: int, sag_type: str, slut_dato: date) -> dict:
    """Gør en sag JSON-venlig til kø-items."""
    return {"sagId": sag_id, "sagType": sag_type, "slutDato": slut_dato.isoformat()}


def fetch_candidates(frist: date) -> dict:
    """Finder sager med en ``SlutDato`` før ``frist``.

    Læser ``formynd.Sag``. Kun ikke-personhenførbare kolonner hentes.

    Args:
        frist: Skæringsdatoen fra ``slet_config.frist``.

    Returns:
        Dict med nøglerne:
            ``kandidater``: Lukkede sager (``ErAktiv = 0``), der skal slettes.
            ``ikkeLukket``: Sager der stadig er aktive (``ErAktiv = 1``),
            fordi lukningen ikke er gennemført. De slettes ikke.
            Hver sag er ``{"sagId", "sagType", "slutDato"}``.
    """
    with db_session() as session:
        rows = session.execute(
            select(Sag.SagId, Sag.SagType, Sag.SlutDato, Sag.ErAktiv)
            .where(Sag.SlutDato < frist)
            .order_by(Sag.SagId)
        ).all()

    result = {
        "kandidater": [_sag(*r[:3]) for r in rows if not r.ErAktiv],
        "ikkeLukket": [_sag(*r[:3]) for r in rows if r.ErAktiv],
    }
    logger.info(
        "Found %d candidates and %d expired sager not closed (frist %s)",
        len(result["kandidater"]),
        len(result["ikkeLukket"]),
        frist,
    )
    return result


def delete_sag(sag_id: int, frist: date) -> dict:
    """Sletter en sag og alle dens rækker i ``formynd``.

    Sletningen kan gentages: findes sagen ikke, men har en slettelog-række,
    gives status ``allerede_slettet``.

    Args:
        sag_id: Sagens id.
        frist: Skæringsdatoen fra ``slet_config.frist``. Sagens ``SlutDato``
            skal ligge før den.

    Returns:
        Dict med nøglerne ``sagId``, ``status`` (``slettet`` eller
        ``allerede_slettet``), ``raekker`` (antal slettede rækker pr. tabel)
        og ``sletningId`` (slettelog-rækkens ``EventId``).

    Raises:
        DeleteRefused: Hvis sagen er aktiv, ikke er udløbet længe nok, eller
            hverken findes eller er slettet før.
    """
    with db_session() as session:
        sag = session.execute(
            select(Sag.SagType, Sag.SlutDato, Sag.ErAktiv)
            .where(Sag.SagId == sag_id)
            .with_hint(Sag, _LOCK_HINT, "mssql")
        ).one_or_none()
        if sag is None:
            return _already_deleted(session, sag_id)

        if sag.ErAktiv:
            raise DeleteRefused(f"Sag {sag_id} er stadig aktiv")
        if sag.SlutDato >= frist:
            raise DeleteRefused(
                f"Sag {sag_id} har SlutDato {sag.SlutDato}, som ikke er før {frist}"
            )

        raekker = {}
        for model in (*CHILD_TABLES, Sag):
            deleted = session.execute(
                delete(model).where(model.SagId == sag_id),
                execution_options={"synchronize_session": False},
            )
            raekker[model.__tablename__] = deleted.rowcount

        payload = {
            "sagType": sag.SagType,
            "slutDato": sag.SlutDato.isoformat(),
            "frist": frist.isoformat(),
            "raekker": raekker,
        }
        sletning = ProcesEventLog(
            SagId=sag_id,
            EventType=SLETTELOG_EVENT_TYPE,
            EventAction=SLETTELOG_EVENT_ACTION,
            EventResult="SUCCESS",
            SourceSystem="PYTHON",
            SourceComponent=slet_config.SOURCE_COMPONENT,
            EventPayload=json.dumps(payload),
            CreatedBy=slet_config.SOURCE_COMPONENT,
        )
        session.add(sletning)
        session.flush()
        sletning_id = sletning.EventId

    logger.info("Deleted sag %s: %s", sag_id, raekker)
    return {
        "sagId": sag_id,
        "status": "slettet",
        "raekker": raekker,
        "sletningId": sletning_id,
    }


def _already_deleted(session: Session, sag_id: int) -> dict:
    """Slår en sag, der ikke findes, op i slettelogen.

    Raises:
        DeleteRefused: Hvis sagen heller ikke står i slettelogen.
    """
    sletning_id = session.scalar(
        select(ProcesEventLog.EventId)
        .where(
            ProcesEventLog.SagId == sag_id,
            ProcesEventLog.EventType == SLETTELOG_EVENT_TYPE,
            ProcesEventLog.EventAction == SLETTELOG_EVENT_ACTION,
        )
        .order_by(ProcesEventLog.EventId)
        .limit(1)
    )
    if sletning_id is None:
        raise DeleteRefused(f"Sag {sag_id} findes ikke")
    return {
        "sagId": sag_id,
        "status": "allerede_slettet",
        "raekker": {},
        "sletningId": sletning_id,
    }
