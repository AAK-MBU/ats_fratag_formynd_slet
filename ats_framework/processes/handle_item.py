"""Behandler sletteprocessens items: sletter sager og sender rapporter."""

import logging
from html import escape

from mbu_rpa_core.exceptions import BusinessError

from ats_framework.helpers.email import send_report_email
from ats_framework.helpers.formynd_db import DeleteRefused, delete_sag
from ats_framework.processes import slet_config

logger = logging.getLogger(__name__)


def _table(headers: list[str], rows: list[list]) -> str:
    """Bygger en HTML-tabel med escapede værdier."""
    head = "".join(f"<th align='left'>{escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>"
        for row in rows
    )
    return f"<table border='1' cellpadding='4' cellspacing='0'><tr>{head}</tr>{body}</table>"


def _sager_table(sager: list[dict]) -> str:
    """Bygger en tabel over sager med id, sagstype og slutdato."""
    return _table(
        ["Sag-id", "Sagstype", "Slutdato"],
        [[s.get("sagId"), s.get("sagType"), s.get("slutDato")] for s in sager],
    )


def _ikke_lukket_html(sager: list[dict]) -> str:
    """Bygger afsnittet om udløbne sager, der ikke er lukket."""
    return (
        f"<p>{len(sager)} sager har en slutdato, der er passeret for mere end "
        f"{slet_config.SLETTE_DAGE} dage siden, men er stadig aktive. Lukningen "
        "(luk-subprocessen i ats_fratag_formynd) er ikke gennemført. Sagerne "
        "slettes ikke, før de er lukket.</p>" + _sager_table(sager)
    )


def handle_slet_sag(data: dict) -> str:
    """Sletter én sag i formynd.

    Args:
        data: Item'ets data med nøglen ``sagId``.

    Returns:
        Besked til det gennemførte item med antal slettede rækker.

    Raises:
        BusinessError: Hvis sagen ikke opfylder slettereglen eller ikke findes.
    """
    sag_id = data["sagId"]
    try:
        result = delete_sag(sag_id, slet_config.frist())
    except DeleteRefused as e:
        raise BusinessError(f"Sletning af sag {sag_id} afvist: {e.reason}") from e

    raekker = result["raekker"]
    message = (
        f"Sag {sag_id}: {result['status']}, "
        f"{sum(raekker.values())} databaserækker "
        f"({', '.join(f'{t}={n}' for t, n in raekker.items()) or '-'}), "
        f"sletning {result['sletningId']}"
    )
    logger.info(message)
    return message


def handle_dry_run(data: dict) -> str:
    """Mailer listen over de sager, en rigtig kørsel ville slette.

    Args:
        data: Item'ets data fra ``populate.build_items`` med ``dry_run``.

    Returns:
        Besked til det gennemførte item.
    """
    kandidater = data.get("kandidater") or []
    ikke_lukket = data.get("ikkeLukket") or []
    html = (
        "<p>Sletteprocessen kører i dry-run og har ikke slettet noget. "
        f"Med sletning slået til ville {len(kandidater)} sager blive slettet: "
        "lukkede sager med en slutdato for mere end "
        f"{escape(str(data.get('dage')))} dage siden "
        f"(før {escape(str(data.get('frist')))}).</p>"
        + _sager_table(kandidater)
        + "<p>Sletning omfatter sagens rækker i Sag, SagESDHReference, "
        "SagNotifikation, SagPartSvar og ProcesEventLog i skemaet formynd på RPA databasen.</p>"
        "<p>Sæt robotten til at slette ved at ændre DRY_RUN=False i "
        "slet_config.py (repo: ats_fratag_formynd_slet).</p>"
    )
    if ikke_lukket:
        html += _ikke_lukket_html(ikke_lukket)

    send_report_email(
        slet_config.subject("dry_run", dato=data.get("dato"), antal=len(kandidater)),
        html,
    )
    return f"Dry-run: {len(kandidater)} kandidater, {len(ikke_lukket)} ikke lukket"


def handle_ikke_lukket(data: dict) -> str:
    """Mailer listen over udløbne sager, der ikke er lukket.

    Args:
        data: Item'ets data fra ``populate.build_items`` med ``sager``.

    Returns:
        Besked til det gennemførte item.
    """
    sager = data.get("sager") or []
    send_report_email(
        slet_config.subject("ikke_lukket", antal=len(sager)), _ikke_lukket_html(sager)
    )
    return f"Rapporteret {len(sager)} udløbne sager, der ikke er lukket"


HANDLERS = {
    "slet_sag": handle_slet_sag,
    "dry_run": handle_dry_run,
    "ikke_lukket": handle_ikke_lukket,
}


def handle_item(item_data: dict, item_reference: str) -> str:
    """Sender et item til handleren for dets type.

    Args:
        item_data: Item'ets data med nøglen ``type``.
        item_reference: Item'ets reference.

    Returns:
        Handlerens besked til det gennemførte item.

    Raises:
        BusinessError: Hvis item'ets type er ukendt, eller sletningen afvises.
    """
    handler = HANDLERS.get(item_data.get("type", "no type defined"))
    if handler is None:
        raise BusinessError(
            f"Unknown item type {item_data.get('type')!r} for {item_reference}"
        )
    message = handler(item_data)
    logger.info("Handled item %s", item_reference)
    return message
