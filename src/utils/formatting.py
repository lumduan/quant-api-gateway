"""Decimal-precise string formatters for OpenBB Metric widget responses.

Three formatters carry the visual contract documented at
https://docs.openbb.co/workspace/developers/widget-types/metric:

* :func:`format_percentage` — value cell ("0.63%" / "-4.22%"), or arrowed
  ("↑ +0.63%") when ``use_arrows=True`` (the arrowed form is for internal
  rendering only; the Metric widget itself expects ``use_arrows=False``).
* :func:`format_currency` — value cell for money ("$998,142.71").
* :func:`format_delta_number` — delta cell: plain signed number as a string
  ("0.12" / "-0.12" / "0.00"). The Metric widget renders arrows and colors
  from the sign; the data carries only the number itself.

All math stays in :class:`Decimal` to preserve fintech precision; the
final f-string format spec works losslessly on Decimal.
"""

from decimal import ROUND_HALF_UP, Decimal

_PERCENT_MULTIPLIER = Decimal(100)


def format_percentage(value: Decimal, decimals: int = 2, use_arrows: bool = True) -> str:
    """Render *value* (a fractional rate, e.g. ``0.0063``) as a percentage string.

    With ``use_arrows=True`` (default): ``↑ +0.63%`` / ``↓ -0.50%`` / ``→ 0.00%``.
    With ``use_arrows=False``: ``0.63%`` / ``-0.50%`` / ``0.00%`` — sign only for
    negatives, matching the unsigned value cell in OpenBB's Metric widget.
    """
    quant = Decimal(1).scaleb(-decimals)
    scaled = (value * _PERCENT_MULTIPLIER).quantize(quant, rounding=ROUND_HALF_UP)
    formatted = f"{scaled:.{decimals}f}"
    if not use_arrows:
        return f"{formatted}%"
    if scaled > 0:
        return f"↑ +{formatted}%"
    if scaled < 0:
        return f"↓ {formatted}%"
    return f"→ {formatted}%"


def format_currency(value: Decimal, currency: str = "USD") -> str:
    """Render *value* as ``$NNN,NNN.NN`` (USD only this iteration).

    Negative values render as ``-$NNN.NN`` rather than ``$-NNN.NN``. The
    ``currency`` argument is reserved for future expansion and currently
    must be ``"USD"``; any other value still emits a ``$`` prefix.
    """
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded < 0:
        return f"-${-rounded:,.2f}"
    return f"${rounded:,.2f}"


def format_delta_number(value: Decimal, decimals: int = 2) -> str:
    """Render *value* as a plain signed number string for OpenBB's delta field.

    Output: ``"0.12"`` (positive, no leading ``+``), ``"-0.12"`` (negative,
    explicit ``-``), ``"0.00"`` (zero). No unit, no thousands separator, no
    arrow. The Metric widget renders the arrow and color based on sign.

    Callers pre-scale percentages to percentage points (multiply by 100)
    so the widget shows ``0.12`` rather than ``0.0012``.
    """
    quant = Decimal(1).scaleb(-decimals)
    rounded = value.quantize(quant, rounding=ROUND_HALF_UP)
    return f"{rounded:.{decimals}f}"
