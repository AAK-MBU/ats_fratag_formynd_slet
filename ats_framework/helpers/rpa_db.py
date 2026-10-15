"""Opslag af konstanter i RPA-databasen.

Læser tabellen ``[rpa].[Constants]`` gennem ORM-modellen ``Constant`` og
``db_session`` i ``ats_framework.helpers.models``, dvs. via
forbindelsesstrengen i miljøvariablen ``DBCONNECTIONSTRINGPROD``.
"""

from sqlalchemy import select

from ats_framework.helpers.models import Constant, db_session


def get_constant(name: str) -> str:
    """Returnerer værdien af en konstant i rpa.Constants.

    Args:
        name: Konstantens navn.

    Returns:
        Konstantens værdi.

    Raises:
        LookupError: Hvis der ikke findes en konstant med det navn.
    """
    with db_session() as session:
        value = session.scalar(select(Constant.value).where(Constant.name == name))
    if value is None:
        raise LookupError(f"Constant {name!r} not found in rpa.Constants")
    return value
