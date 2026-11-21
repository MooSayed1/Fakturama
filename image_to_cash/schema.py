"""The schema of an order: every field we read from the image, with its type.

These classes do two jobs:
1. They are the JSON schema we give the LLM, so its answer has exactly these fields.
2. They parse that answer into Python objects with real types (Decimal, date),
   which the rest of the program passes around as an `Order`.
"""
import datetime  # module import: a field below is itself called "date"
from decimal import Decimal

from pydantic import BaseModel, Field


class Address(BaseModel):
    name: str = Field(description="First line of the address block, e.g. the company or site name")
    street: str
    zip: str
    city: str
    country: str


class Debtor(BaseModel):
    company: str
    alias: str | None = Field(description="Customer alias, if printed")
    first_name: str = Field(description="First name of the contact person")
    last_name: str = Field(description="Last name of the contact person")
    email: str | None
    phone: str | None


class Payment(BaseModel):
    method: str = Field(description="Payment method exactly as printed, e.g. 'Bank Transfer'")
    status: str = Field(description="'PAID' or 'UNPAID', in capitals")
    date: datetime.date | None = Field(description="Payment date, or null if not printed")


class Item(BaseModel):
    sku: str
    description: str
    qty: Decimal
    unit: str | None
    unit_net: Decimal = Field(description="Unit price before VAT")
    discount_pct: Decimal = Field(description="Line discount in percent, e.g. 10 for 10%")
    vat_pct: Decimal = Field(description="VAT rate in percent, e.g. 19 for 19%")
    line_net: Decimal = Field(description="Line total before VAT, as printed")


class Totals(BaseModel):
    net: Decimal
    vat: Decimal
    gross: Decimal


class Order(BaseModel):
    external_reference: str
    order_date: datetime.date
    customer_id: str | None = Field(description="Customer ID printed on the image (not used later)")
    currency: str = Field(description="ISO code, e.g. 'EUR'")
    debtor: Debtor
    billing_address: Address
    delivery_address: Address
    payment: Payment
    items: list[Item] = Field(description="Every item line, in the order printed")
    totals: Totals
