"""Læsning og sletning af sager i ``[RPA].[formynd]``.

Går gennem ``mbu_rpa_core.database.connection.RPAConnection`` og dermed
forbindelsesstrengen i miljøvariablen ``DBCONNECTIONSTRINGPROD``. Kontoen skal
have SELECT, INSERT og DELETE på skemaet ``formynd``.

En sag slettes i én transaktion: reglen tjekkes igen med lås på sagen, alle
sagens rækker slettes, og sletningen skrives i ``formynd.ProcesEventLog`` som
en slettelog-række uden personoplysninger. Fejler et trin, rulles det hele
tilbage.
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

from ats_framework.processes import slet_config

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import date

logger = logging.getLogger(__name__)

# Tabellerne med sagens rækker ud over formynd.Sag, i sletterækkefølge. De tre
# første har en fremmednøgle til Sag. ProcesEventLog har ingen, men dens
# payloads indeholder de mails, der er sendt om sagen. En ny tabel med
# fremmednøgle til Sag, der ikke står her, får DELETE på Sag til at fejle, og
# så rulles sletningen tilbage.
CHILD_TABLES = ("SagPartSvar", "SagNotifikation", "SagESDHReference", "ProcesEventLog")

SLETTELOG_EVENT_TYPE = "SLETNING"
SLETTELOG_EVENT_ACTION = "SAG_SLETTET"


class DeleteRefused(Exception):
    """Sagen kan ikke slettes.

    Attributes:
        reason: Hvorfor sagen ikke blev slettet.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@contextmanager
def _transaction() -> Iterator:
    """Åbner en transaktion mod RPA-databasen og giver dens cursor.

    Transaktionen committes, når blokken afsluttes uden fejl, og rulles
    tilbage ved en fejl.

    Yields:
        En pyodbc-cursor.
    """
    from mbu_rpa_core.database.connection import RPAConnection  # noqa: PLC0415

    with RPAConnection(db_env="PROD", commit=False) as conn:
        yield conn.cursor
        conn.conn.commit()


def _rows(cursor) -> list[dict]:
    """Omsætter cursorens resultat til dicts nøglet på kolonnenavn."""
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _sag(row: dict) -> dict:
    """Gør en sagsrække JSON-venlig til kø-items."""
    return {
        "sagId": row["SagId"],
        "sagType": row["SagType"],
        "slutDato": row["SlutDato"].isoformat(),
    }


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
    with _transaction() as cursor:
        cursor.execute(
            "SELECT SagId, SagType, SlutDato, ErAktiv FROM formynd.Sag "
            "WHERE SlutDato < ? ORDER BY SagId",
            frist,
        )
        rows = _rows(cursor)

    result = {
        "kandidater": [_sag(r) for r in rows if not r["ErAktiv"]],
        "ikkeLukket": [_sag(r) for r in rows if r["ErAktiv"]],
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
    with _transaction() as cursor:
        cursor.execute(
            "SELECT SagType, SlutDato, ErAktiv FROM formynd.Sag "
            "WITH (UPDLOCK, HOLDLOCK) WHERE SagId = ?",
            sag_id,
        )
        rows = _rows(cursor)
        if not rows:
            return _already_deleted(cursor, sag_id)

        sag = rows[0]
        if sag["ErAktiv"]:
            raise DeleteRefused(f"Sag {sag_id} er stadig aktiv")
        if sag["SlutDato"] >= frist:
            raise DeleteRefused(
                f"Sag {sag_id} har SlutDato {sag['SlutDato']}, som ikke er før {frist}"
            )

        raekker = {}
        for table in (*CHILD_TABLES, "Sag"):
            cursor.execute(f"DELETE FROM formynd.{table} WHERE SagId = ?", sag_id)
            raekker[table] = cursor.rowcount

        payload = {
            "sagType": sag["SagType"],
            "slutDato": sag["SlutDato"].isoformat(),
            "frist": frist.isoformat(),
            "raekker": raekker,
        }
        cursor.execute(
            "INSERT INTO formynd.ProcesEventLog "
            "(SagId, EventType, EventAction, EventResult, SourceSystem, "
            "SourceComponent, EventPayload, CreatedBy) "
            "OUTPUT INSERTED.EventId "
            "VALUES (?, ?, ?, 'SUCCESS', 'PYTHON', ?, ?, ?)",
            sag_id,
            SLETTELOG_EVENT_TYPE,
            SLETTELOG_EVENT_ACTION,
            slet_config.SOURCE_COMPONENT,
            json.dumps(payload),
            slet_config.SOURCE_COMPONENT,
        )
        sletning_id = cursor.fetchone()[0]

    logger.info("Deleted sag %s: %s", sag_id, raekker)
    return {
        "sagId": sag_id,
        "status": "slettet",
        "raekker": raekker,
        "sletningId": sletning_id,
    }


def _already_deleted(cursor, sag_id: int) -> dict:
    """Slår en sag, der ikke findes, op i slettelogen.

    Raises:
        DeleteRefused: Hvis sagen heller ikke står i slettelogen.
    """
    cursor.execute(
        "SELECT TOP (1) EventId FROM formynd.ProcesEventLog "
        "WHERE SagId = ? AND EventType = ? AND EventAction = ? ORDER BY EventId",
        sag_id,
        SLETTELOG_EVENT_TYPE,
        SLETTELOG_EVENT_ACTION,
    )
    row = cursor.fetchone()
    if row is None:
        raise DeleteRefused(f"Sag {sag_id} findes ikke")
    return {
        "sagId": sag_id,
        "status": "allerede_slettet",
        "raekker": {},
        "sletningId": row[0],
    }
