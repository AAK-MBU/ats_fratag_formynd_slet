"""Helper module to call some functionality in Automation Server using the API."""

import logging
import os
from collections.abc import Iterator
from functools import cache
from typing import Literal, overload

import requests
from automation_server_client import AutomationServerConfig, WorkItem, Workqueue
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

PAGE_SIZE = 200  # maks. tilladt sidestørrelse i ATS


def _ats_env() -> None:
    """Læser ATS' URL og token fra miljøet.

    Læses ved kaldet og ikke ved import, så modulet kan importeres fra tests,
    hvor ``load_dotenv()`` ikke er kørt.

    Raises:
        OSError: Hvis ``ATS_URL`` eller ``ATS_TOKEN`` ikke er sat i miljøet.
    """
    load_dotenv()

    url = os.getenv("ATS_URL")
    token = os.getenv("ATS_TOKEN")

    if not url or not token:
        raise OSError("ATS_URL or ATS_TOKEN is not set in the environment")

    AutomationServerConfig.url = url
    AutomationServerConfig.token = token


def _headers() -> dict[str, str]:
    """Returnerer Authorization-headeren til ATS-API'et."""
    return {"Authorization": f"Bearer {AutomationServerConfig.token}"}


@cache
def fetch_workqueue(workqueue_name: str) -> Workqueue:
    """Slår en workqueue op på navn via ATS-API'et.

    Kalder ``GET {ATS_URL}/workqueues/by_name/{navn}`` for at oversætte navnet
    til et kø-id og henter derefter køen med ``Workqueue.get_workqueue``.
    Resultatet caches pr. navn, så en kørsel kun slår hver kø op én gang.

    Opslaget går bevidst ikke gennem ``AutomationServer.from_environment()``,
    da den overskriver ``ATS_WORKQUEUE_OVERRIDE`` og hægter en ny
    logging-handler på root-loggeren ved hvert kald.

    Args:
        workqueue_name: Køens navn i ATS.

    Returns:
        Workqueue-instansen for navnet.

    Raises:
        OSError: Hvis ``ATS_URL`` eller ``ATS_TOKEN`` ikke er sat i miljøet.
        requests.HTTPError: Hvis ATS ikke kender køen (404) eller afviser kaldet.
    """
    _ats_env()

    response = requests.get(
        f"{AutomationServerConfig.url}/workqueues/by_name/{workqueue_name}",
        headers=_headers(),
        timeout=60,
    )
    response.raise_for_status()

    workqueue = Workqueue.get_workqueue(response.json()["id"])
    logger.info("Resolved workqueue %r -> id %s", workqueue_name, workqueue.id)
    return workqueue


def iter_workqueue_rows(workqueue: Workqueue) -> Iterator[dict]:
    """Giver alle items i en workqueue som rå rækker fra ATS-API'et.

    Kalder ``GET {ATS_URL}/workqueues/{id}/items`` side for side, til en side
    kommer tilbage tom.

    Args:
        workqueue: Køen der skal læses.

    Yields:
        Én dict pr. item med ATS' felter, bl.a. ``id``, ``reference``,
        ``status``, ``locked``, ``created_at`` og ``updated_at`` (ISO-strenge
        i ATS-serverens lokaltid uden tidszone).

    Raises:
        OSError: Hvis ``ATS_URL`` eller ``ATS_TOKEN`` ikke er sat i miljøet.
        requests.HTTPError: Hvis ATS afviser kaldet.
    """
    _ats_env()

    page = 1
    while True:
        response = requests.get(
            f"{AutomationServerConfig.url}/workqueues/{workqueue.id}/items",
            params={"page": page, "size": PAGE_SIZE},
            headers=_headers(),
            timeout=60,
        )
        response.raise_for_status()

        rows = response.json().get("items", [])
        if not rows:
            return

        yield from rows
        page += 1


@overload
def get_workqueue_items(
    workqueue: Workqueue, return_data: Literal[False] = False
) -> set[str]: ...


@overload
def get_workqueue_items(
    workqueue: Workqueue, return_data: Literal[True]
) -> dict[str, dict]: ...


def get_workqueue_items(
    workqueue: Workqueue, return_data: bool = False
) -> set[str] | dict[str, dict]:
    """Henter alle items i en workqueue, nøglet på reference.

    Args:
        workqueue: Køen der skal læses.
        return_data: Om hele rækken skal returneres i stedet for kun
            referencerne.

    Returns:
        Dict fra reference til række, hvis ``return_data`` er sat; ellers et
        set af referencer. Items uden reference udelades.
    """
    workqueue_items = {
        row["reference"]: row
        for row in iter_workqueue_rows(workqueue)
        if row.get("reference")
    }
    return workqueue_items if return_data else set(workqueue_items)


def set_workitem_status(item_id: int, status: str, message: str = "") -> None:
    """Sætter status på et workitem via ``PUT {ATS_URL}/workitems/{id}/status``.

    ATS låser elementet op, når status sættes til ``new``, ``completed``,
    ``failed`` eller ``pending user action``, og opdaterer ``updated_at``.

    Args:
        item_id: Workitem'ets id i ATS.
        status: Den nye status, fx ``"new"``.
        message: Besked der gemmes på elementet.

    Raises:
        OSError: Hvis ``ATS_URL`` eller ``ATS_TOKEN`` ikke er sat i miljøet.
        requests.HTTPError: Hvis ATS afviser kaldet.
    """
    _ats_env()

    response = requests.put(
        f"{AutomationServerConfig.url}/workitems/{item_id}/status",
        headers=_headers(),
        json={"status": status, "message": message},
        timeout=60,
    )
    response.raise_for_status()
    logger.info("Set workitem %s to status %r", item_id, status)


def get_item_info(item: WorkItem):
    """Unpack item"""
    return item.data["data"], item.reference


def init_logger():
    """Initialize the root logger with JSON formatting."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(module)s.%(funcName)s:%(lineno)d — %(message)s",
        datefmt="%H:%M:%S",
    )
