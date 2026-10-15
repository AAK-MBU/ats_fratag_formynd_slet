"""SMTP-afsendelse af rapport-mails.

Modtagere, afsender og SMTP-opsætning læses i rpa.Constants under de navne,
``ats_framework.processes.slet_config`` angiver.

Ved lokal udvikling (``LOCAL_DEVELOPMENT=true``) mailes der aldrig til en reel
modtager: alle modtagere omdirigeres til miljøadressen ``TestEmail``, og
brødteksten indledes med en linje, der navngiver de modtagere, mailen ville
være gået til i drift.
"""

import json
import logging
import os
import smtplib
from email.message import EmailMessage

from ats_framework.helpers.rpa_db import get_constant
from ats_framework.processes import slet_config

logger = logging.getLogger(__name__)


def _test_mode() -> bool:
    """True hvis processen kører lokalt (``LOCAL_DEVELOPMENT=true``)."""
    return os.getenv("LOCAL_DEVELOPMENT", "").lower() == "true"


def parse_recipients(value: str) -> list[str]:
    """Omsætter en modtagerkonstant til en liste af adresser.

    Args:
        value: Enten en JSON-liste af adresser eller én eller flere adresser
            adskilt af komma eller semikolon.

    Returns:
        Listen af adresser uden tomme elementer.
    """
    value = value.strip()
    if value.startswith("["):
        return [str(a).strip() for a in json.loads(value) if str(a).strip()]
    return [a.strip() for a in value.replace(";", ",").split(",") if a.strip()]


def send_report_email(subject: str, html_body: str) -> list[str]:
    """Sender én HTML-mail til rapportmodtagerne.

    Args:
        subject: Emnelinjen.
        html_body: Mailens brødtekst som HTML.

    Returns:
        De adresser, mailen faktisk blev sendt til.

    Raises:
        ValueError: Hvis modtagerkonstanten er tom, eller hvis processen kører
            lokalt uden ``TestEmail``.
    """
    recipients = parse_recipients(get_constant(slet_config.RECIPIENTS_CONSTANT))
    if not recipients:
        raise ValueError(f"Constant {slet_config.RECIPIENTS_CONSTANT!r} is empty")

    if _test_mode():
        test_email = os.getenv("TestEmail")  # noqa: SIM112
        if not test_email:
            raise ValueError("Set env var TestEmail to send emails locally")
        html_body = f"<p>Modtagere i prod: {', '.join(recipients)}</p>" + html_body
        recipients = [test_email]

    msg = EmailMessage()
    msg["To"] = ", ".join(recipients)
    msg["From"] = get_constant(slet_config.SENDER_CONSTANT)
    msg["Subject"] = subject
    msg.set_content("Din mailklient understøtter ikke HTML.")
    msg.add_alternative(html_body, subtype="html")

    smtp_server = get_constant(slet_config.SMTP_SERVER_CONSTANT)
    smtp_port = int(get_constant(slet_config.SMTP_PORT_CONSTANT))
    with smtplib.SMTP(smtp_server, smtp_port) as smtp:
        smtp.starttls()
        smtp.send_message(msg)

    logger.info("Sent %r to %s", subject, recipients)
    return recipients
