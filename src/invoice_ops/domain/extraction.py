"""The strict shape of an extracted invoice.

Every field is optional: the model must be free to say "not present" instead of
inventing a value. Whether a missing field is acceptable is a downstream
decision (matching in M3, validation in M4), not this schema's job.

Amounts are ``Decimal`` -- never float -- because this is money.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# Fields whose confidence gates auto-processing later. Kept here so the eval
# harness and the (future) review router agree on what "the important fields"
# are.
CRITICAL_FIELDS: frozenset[str] = frozenset(
    {"supplier_name", "invoice_number", "invoice_date", "currency", "total"}
)


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class ExtractedInvoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: str | None = None
    supplier_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    currency: str | None = Field(default=None, description="ISO 4217, e.g. EUR")
    purchase_order_number: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    total: Decimal | None = None


class ExtractionResult(BaseModel):
    """What the model is asked to return: the invoice plus its own confidence."""

    model_config = ConfigDict(extra="forbid")

    invoice: ExtractedInvoice
    field_confidence: dict[str, float] = Field(
        default_factory=dict,
        description="0..1 per field name in `invoice` the model is confident about",
    )
    notes: str | None = Field(
        default=None, description="free-text caveats about anything ambiguous"
    )


def result_json_schema() -> dict[str, object]:
    """JSON Schema handed to the provider's structured-output mode."""
    return ExtractionResult.model_json_schema()
