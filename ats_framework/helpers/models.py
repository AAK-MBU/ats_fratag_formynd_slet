"""SQLAlchemy-modeller samt engine/session for RPA-databasen.

Al adgang til ``formynd``-skemaet går gennem dette modul og
``ats_framework.helpers.formynd_db`` — aldrig rå SQL-strenge. Konstanter i
``rpa.Constants`` læses via ``RPAConnection`` (se
``ats_framework.helpers.rpa_db``).

Modellerne er de tabeller i ``formynd``-skemaet, sletningen rører (``Sag`` og
de tabeller, der bærer et ``SagId``). Kolonnerne er
de samme som modellerne i ats_fratag_formynd's ``helpers/models.py``, som ejer
skemaet (DDL'en ligger i ats_fratag_formynd's ``db/20_tabeller.sql``).

Engine'en bygges lazily ved første brug (``db_session``), så det at importere
modulet aldrig kræver en tilgængelig database.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import (  # noqa: TC003  (runtime-needed by SQLAlchemy Mapped[...])
    date,
    datetime,
)
from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

SCHEMA = "formynd"

# Miljøvariablen med RPA-databasens connection-string.
CONNECTION_STRING_ENV = "DBCONNECTIONSTRINGPROD"


class Base(DeclarativeBase):
    """Fælles deklarativ basisklasse for alle ORM-modellerne i dette modul."""


class Sag(Base):
    """Sagens stamdata: sagstype, cpr-numre, forløbsdatoer og om sagen er aktiv.

    Tabellen ``formynd.Sag`` — den centrale sagstabel, de øvrige
    formynd-tabeller refererer til via ``SagId``.
    """

    __tablename__ = "Sag"
    __table_args__ = {"schema": SCHEMA}

    SagId: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SagType: Mapped[str] = mapped_column(String(50), nullable=False)
    BarnCPR: Mapped[str] = mapped_column(String(11), nullable=False)
    Foraeldre1CPR: Mapped[str] = mapped_column(String(11), nullable=False)
    Foraeldre2CPR: Mapped[str | None] = mapped_column(String(11))
    StartDato: Mapped[date] = mapped_column(Date, nullable=False)
    SlutDato: Mapped[date] = mapped_column(Date, nullable=False)
    ErAktiv: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1")
    )
    OprettetDato: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("SYSUTCDATETIME()")
    )
    SidstOpdateretDato: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("SYSUTCDATETIME()")
    )
    SidstOpdateretAf: Mapped[str | None] = mapped_column(String(100))
    AarsagAendring: Mapped[str | None] = mapped_column(Text)
    OprettetAf: Mapped[str | None] = mapped_column(String(200))
    OprettetFraFormId: Mapped[str | None] = mapped_column(String(50))


class SagESDHReference(Base):
    """ESDH-sagsreferencen for en formynd-sag (tabellen ``formynd.SagESDHReference``)."""

    __tablename__ = "SagESDHReference"
    __table_args__ = {"schema": SCHEMA}

    SagESDHReferenceId: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    SagId: Mapped[int] = mapped_column(Integer, nullable=False)
    FormId: Mapped[str] = mapped_column(String(100), nullable=False)
    ESDHSystem: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'ESDH'")
    )
    ESDHId: Mapped[str] = mapped_column(String(100), nullable=False)
    OprettetDato: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("SYSUTCDATETIME()")
    )
    OprettetAf: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("'Integration'")
    )


class SagNotifikation(Base):
    """En sendt notifikation om sagen (tabellen ``formynd.SagNotifikation``)."""

    __tablename__ = "SagNotifikation"
    __table_args__ = {"schema": SCHEMA}

    SagNotifikationId: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    SagId: Mapped[int] = mapped_column(Integer, nullable=False)
    NotifikationType: Mapped[str] = mapped_column(String(50), nullable=False)
    SlutDato: Mapped[date] = mapped_column(Date, nullable=False)
    NotificeretDato: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    SourceComponent: Mapped[str] = mapped_column(String(100), nullable=False)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("SYSUTCDATETIME()")
    )
    PartId: Mapped[int | None] = mapped_column(Integer)
    PartNavn: Mapped[str | None] = mapped_column(String(200))


class SagPartSvar(Base):
    """En parts bekræftelse af en notifikation (tabellen ``formynd.SagPartSvar``)."""

    __tablename__ = "SagPartSvar"
    __table_args__ = {"schema": SCHEMA}

    SagPartSvarId: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    SagId: Mapped[int] = mapped_column(Integer, nullable=False)
    PartId: Mapped[int | None] = mapped_column(Integer)
    NotifikationType: Mapped[str] = mapped_column(String(50), nullable=False)
    SlutDato: Mapped[date] = mapped_column(Date, nullable=False)
    Svar: Mapped[str] = mapped_column(String(20), nullable=False)
    BesvaretAf: Mapped[str | None] = mapped_column(String(200))
    FormSubmissionId: Mapped[str] = mapped_column(String(100), nullable=False)
    Kommentar: Mapped[str | None] = mapped_column(Text)
    RaaSvar: Mapped[str | None] = mapped_column(Text)
    SvarDato: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("SYSUTCDATETIME()")
    )


class ProcesEventLog(Base):
    """Audit-/eventlog (tabellen ``formynd.ProcesEventLog``).

    Rummer sagens notifikations-payloads og sletteprocessens slettelog-rækker.
    """

    __tablename__ = "ProcesEventLog"
    __table_args__ = {"schema": SCHEMA}

    EventId: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    SagId: Mapped[int | None] = mapped_column(Integer)
    EventType: Mapped[str] = mapped_column(String(50), nullable=False)
    EventAction: Mapped[str] = mapped_column(String(100), nullable=False)
    EventResult: Mapped[str | None] = mapped_column(String(50))
    SourceSystem: Mapped[str] = mapped_column(String(50), nullable=False)
    SourceComponent: Mapped[str | None] = mapped_column(String(100))
    SourceInstanceId: Mapped[str | None] = mapped_column(String(100))
    DataFoer: Mapped[str | None] = mapped_column(Text)
    DataEfter: Mapped[str | None] = mapped_column(Text)
    EventPayload: Mapped[str | None] = mapped_column(Text)
    ErrorCode: Mapped[str | None] = mapped_column(String(50))
    ErrorMessage: Mapped[str | None] = mapped_column(String(4000))
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("SYSUTCDATETIME()")
    )
    CreatedBy: Mapped[str | None] = mapped_column(String(100))


def _to_sqlalchemy_url(conn: str) -> str:
    """Accepterer enten en SQLAlchemy-URL eller en rå ODBC connection-streng.

    En SQLAlchemy-URL (``mssql+pyodbc://...`` eller ``sqlite://``) returneres
    uændret; en rå ODBC-streng (``Driver={...};Server=...``) pakkes ind via
    pyodbc's ``odbc_connect``-escape.

    Args:
        conn: Connection-strengen fra miljøvariablen ``DBCONNECTIONSTRINGPROD``.

    Returns:
        En SQLAlchemy-forbindelses-URL, klar til ``create_engine``.

    Raises:
        RuntimeError: Hvis ``conn`` er tom.
    """
    if not conn:
        raise RuntimeError(f"{CONNECTION_STRING_ENV} is not set (see .env.example)")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", conn):
        return conn
    return "mssql+pyodbc:///?odbc_connect=" + quote_plus(conn)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    """Bygger engine og session-factory én gang, ved første brug."""
    engine = create_engine(
        _to_sqlalchemy_url(os.getenv(CONNECTION_STRING_ENV, "")),
        pool_pre_ping=True,
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def db_session() -> Iterator[Session]:
    """Kortlivet transaktionel session: commit ved succes, rollback ved fejl.

    Yields:
        En SQLAlchemy-session mod RPA-databasen.
    """
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
