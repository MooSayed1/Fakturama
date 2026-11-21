"""Checks on the extracted order. Fakturama is only touched if all of them pass."""
import re
from decimal import ROUND_HALF_UP, Decimal

from .errors import ManualReview
from .schema import Item, Order

CENT = Decimal("0.01")

# Payment method on the image -> payment code in Fakturama (from the brief).
PAYMENT_CODES = {
    "Bank Transfer": "Credit transfer",
    "Credit Card": "Credit card",
    "SEPA Direct Debit": "SEPA direct debit",
}


def money(value) -> Decimal:
    """Round to cents, half up, the way invoices round."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def expected_line_net(item: Item) -> Decimal:
    """qty x unit net price, minus the line discount."""
    return money(item.qty * item.unit_net * (1 - item.discount_pct / 100))


def gross_list_price(item: Item) -> Decimal:
    """The Product's price in Fakturama: net plus VAT, without the line discount."""
    return money(item.unit_net * (1 + item.vat_pct / 100))


def find_problems(order: Order) -> list[str]:
    """Return a list of human-readable problems. An empty list means the order is OK."""
    problems = []

    # 1. Presence and format
    if not order.external_reference.strip():
        problems.append("external reference is empty")
    if not order.items:
        problems.append("the order has no items")
    email = order.debtor.email
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        problems.append(f"email {email!r} does not look like an email address")
    for label, address in [("billing", order.billing_address), ("delivery", order.delivery_address)]:
        if not re.fullmatch(r"[A-Za-z0-9 -]{3,10}", address.zip):
            problems.append(f"{label} ZIP {address.zip!r} looks wrong")
    for number, item in enumerate(order.items, start=1):
        if item.qty <= 0:
            problems.append(f"item {number} ({item.sku}): quantity {item.qty} must be positive")
        if item.unit_net < 0:
            problems.append(f"item {number} ({item.sku}): negative price {item.unit_net}")
        if not 0 <= item.discount_pct < 100:
            problems.append(f"item {number} ({item.sku}): discount {item.discount_pct}% is out of range")

    # 2. Line math: every printed line total must match qty x price - discount
    for number, item in enumerate(order.items, start=1):
        expected = expected_line_net(item)
        if expected != money(item.line_net):
            problems.append(
                f"item {number} ({item.sku}): {item.qty} x {item.unit_net} - {item.discount_pct}% "
                f"= {expected}, but the image says {item.line_net}")

    # 3. Total math
    net = sum((money(item.line_net) for item in order.items), Decimal(0))
    vat = money(sum((item.line_net * item.vat_pct / 100 for item in order.items), Decimal(0)))
    if net != money(order.totals.net):
        problems.append(f"lines add up to net {net}, but the image says {order.totals.net}")
    if vat != money(order.totals.vat):
        problems.append(f"VAT should be {vat}, but the image says {order.totals.vat}")
    if money(order.totals.net) + money(order.totals.vat) != money(order.totals.gross):
        problems.append(f"net {order.totals.net} + VAT {order.totals.vat} "
                        f"is not the gross {order.totals.gross}")

    # 4. Business rules
    if order.currency != "EUR":
        problems.append(f"currency {order.currency!r} is not supported (only EUR)")
    if order.payment.method not in PAYMENT_CODES:
        problems.append(f"payment method {order.payment.method!r} has no known Fakturama code")
    if order.payment.status not in ("PAID", "UNPAID"):
        problems.append(f"payment status {order.payment.status!r} is neither PAID nor UNPAID")
    if order.payment.status == "PAID" and order.payment.date is None:
        problems.append("the order is PAID but has no payment date")

    return problems


def check(order: Order) -> None:
    """Stop the run if anything is wrong."""
    problems = find_problems(order)
    if problems:
        raise ManualReview("the extracted order failed validation:\n  - " + "\n  - ".join(problems))
