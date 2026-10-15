"""Opslag af konstanter i RPA-databasen.

Går gennem ``mbu_rpa_core.database.connection.RPAConnection``, som læser
``[rpa].[Constants]`` via forbindelsesstrengen i
miljøvariablen ``DBCONNECTIONSTRINGPROD``. ``RPAConnection`` importeres først
ved kaldet, da den trækker pyodbc og dermed en ODBC-driver med sig.
"""


def get_constant(name: str) -> str:
    """Returnerer værdien af en konstant i rpa.Constants.

    Args:
        name: Konstantens navn.

    Returns:
        Konstantens værdi.
    """
    from mbu_rpa_core.database.connection import RPAConnection  # noqa: PLC0415

    with RPAConnection(db_env="PROD", commit=False) as conn:
        return conn.get_constant(name)["value"]
