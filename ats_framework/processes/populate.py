"""Samler items til sletteprocessens kø ud fra de udløbne sager i formynd."""

import logging
from datetime import datetime

from automation_server_client import Workqueue

from ats_framework.helpers.formynd_db import fetch_candidates
from ats_framework.processes import slet_config

logger = logging.getLogger(__name__)


def build_items(candidates: dict, today: str, frist: str, dry_run: bool) -> list[dict]:
    """Bygger kø-items ud fra kandidatlisten.

    Med ``dry_run`` bygges ét item ``dryrun_<dato>`` af typen ``dry_run`` med
    både kandidaterne og de udløbne sager, der ikke er lukket. Ellers bygges
    ét item ``slet_sag_<SagId>`` af typen ``slet_sag`` pr. kandidat og, når
    der er udløbne sager der ikke er lukket, ét item ``ikke_lukket_<dato>`` af
    typen ``ikke_lukket``.

    Args:
        candidates: Svaret fra ``formynd_db.fetch_candidates``.
        today: Kørselsdatoen som ``YYYY-MM-DD``. Indgår i referencerne på
            rapport-items, så de oprettes én gang pr. dag.
        frist: Skæringsdatoen som ``YYYY-MM-DD``.
        dry_run: Om processen kun skal rapportere.

    Returns:
        Items som ``{"reference": ..., "data": ...}``.
    """
    kandidater = candidates.get("kandidater") or []
    ikke_lukket = candidates.get("ikkeLukket") or []

    if dry_run:
        if not kandidater and not ikke_lukket:
            return []
        return [
            {
                "reference": f"dryrun_{today}",
                "data": {
                    "type": "dry_run",
                    "dato": today,
                    "dage": slet_config.SLETTE_DAGE,
                    "frist": frist,
                    "kandidater": kandidater,
                    "ikkeLukket": ikke_lukket,
                },
            }
        ]

    items = [
        {
            "reference": f"slet_sag_{k['sagId']}",
            "data": {"type": "slet_sag", **k},
        }
        for k in kandidater
    ]
    if ikke_lukket:
        items.append(
            {
                "reference": f"ikke_lukket_{today}",
                "data": {"type": "ikke_lukket", "dato": today, "sager": ikke_lukket},
            }
        )
    return items


def collect_items(workqueue: Workqueue) -> list[dict]:
    """Finder de udløbne sager i formynd og bygger kø-items.

    Args:
        workqueue: Sletteprocessens kø.

    Returns:
        Items som ``{"reference": ..., "data": ...}``.
    """
    today = datetime.now(slet_config.TIMEZONE).date()
    frist = slet_config.frist(today)
    candidates = fetch_candidates(frist)

    items = build_items(
        candidates, today.isoformat(), frist.isoformat(), slet_config.DRY_RUN
    )
    logger.info(
        "Built %d items for %s (dry run: %s)",
        len(items),
        workqueue.name,
        slet_config.DRY_RUN,
    )
    return items
