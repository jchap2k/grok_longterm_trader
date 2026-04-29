"""Helpers for reading share quantities from broker position objects."""


def get_position_quantity(position) -> int:
    """Return normalized share quantity from broker positions exposing `quantity` or `qty`."""
    if position is None:
        return 0

    raw_qty = getattr(position, "quantity", getattr(position, "qty", 0))
    try:
        return int(float(raw_qty or 0))
    except (TypeError, ValueError):
        return 0
