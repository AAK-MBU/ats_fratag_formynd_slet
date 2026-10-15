"""Konfiguration af sletteprocessen: slettereglen, dry-run og mails.

Slettereglen ejes af denne proces: en formynd-sag slettes, når den er lukket
(``ErAktiv = 0``) og dens ``SlutDato`` ligger mere end ``SLETTE_DAGE`` dage
tilbage. Reglen tjekkes både, når kandidaterne findes, og igen i den
transaktion, der sletter sagen.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PROCESS_NAME = "ats_fratag_formynd_slet"

# Med DRY_RUN sletter processen intet. Populate lægger ét rapport-item i køen,
# og behandlingen af det mailer listen over de sager, der ville blive slettet.
DRY_RUN = True

# En sag slettes, når dens SlutDato ligger mere end så mange dage tilbage.
SLETTE_DAGE = 30
TIMEZONE = ZoneInfo("Europe/Copenhagen")

# Skrives som SourceComponent/CreatedBy på slettelog-rækken i
# formynd.ProcesEventLog.
SOURCE_COMPONENT = PROCESS_NAME

# ----------------------
# Rapport-mails (navne på konstanter i rpa.Constants)
# ----------------------
RECIPIENTS_CONSTANT = "E-mail"  # JSON-liste eller kommasepareret
SENDER_CONSTANT = "e-mail_noreply"
SMTP_SERVER_CONSTANT = "smtp_adm_server"
SMTP_PORT_CONSTANT = "smtp_port"

SUBJECT_PREFIX = "Fratag forældremyndighed sletning"
# Emneskabeloner. Formatteres med item'ets felter.
SUBJECTS = {
    "dry_run": "Dry-run {dato}: {antal} sager ville blive slettet",
    "ikke_lukket": "{antal} udløbne sager er ikke lukket og slettes ikke",
}


def frist(today: date | None = None) -> date:
    """Returnerer skæringsdatoen for sletning.

    En lukket sag slettes, når dens ``SlutDato`` ligger før denne dato.

    Args:
        today: Dagens dato. Udelades den, bruges dags dato i ``TIMEZONE``.

    Returns:
        ``today`` minus ``SLETTE_DAGE`` dage.
    """
    today = today or datetime.now(TIMEZONE).date()
    return today - timedelta(days=SLETTE_DAGE)


def subject(key: str, **fields: object) -> str:
    """Bygger emnelinjen for en rapport-mail.

    Args:
        key: Nøglen i ``SUBJECTS``.
        **fields: Værdierne, skabelonen formatteres med.

    Returns:
        ``SUBJECT_PREFIX`` efterfulgt af den formatterede skabelon.
    """
    return f"{SUBJECT_PREFIX}: {SUBJECTS[key].format(**fields)}"
