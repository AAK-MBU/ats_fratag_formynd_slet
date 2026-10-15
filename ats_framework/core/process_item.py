"""Module to handle item processing"""

from ats_framework.processes.handle_item import handle_item


def process_item(item_data: dict, item_reference: str) -> str | None:
    """Function to handle item processing.

    Returns:
        Besked der gemmes på det gennemførte item, eller ``None`` for
        standardbeskeden.
    """
    return handle_item(item_data, item_reference)
