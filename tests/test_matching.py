from __future__ import annotations

import uuid

from invoice_ops.domain.extraction import ExtractedInvoice
from invoice_ops.domain.matching import (
    PoIdentity,
    VendorIdentity,
    match_po,
    match_vendor,
)

ACME_ID = uuid.uuid4()
OTHER_ID = uuid.uuid4()

VENDORS = (
    VendorIdentity(
        vendor_id=ACME_ID,
        legal_name="ACME Supplies Ltd",
        tax_id="IT 01234567890",
        aliases=("ACME",),
    ),
    VendorIdentity(
        vendor_id=OTHER_ID,
        legal_name="ACME Consulting Ltd",
        tax_id="IT99999999999",
    ),
)


def _inv(**kw) -> ExtractedInvoice:
    return ExtractedInvoice(**kw)


def test_tax_id_exact_match_wins_even_with_noisy_formatting():
    result = match_vendor(_inv(supplier_tax_id="it-01234567890"), VENDORS)
    assert result.vendor_id == ACME_ID
    assert result.method == "tax_id"
    assert result.confidence == 1.0


def test_exact_name_match():
    result = match_vendor(_inv(supplier_name="acme supplies ltd"), VENDORS)
    assert result.vendor_id == ACME_ID
    assert result.method == "exact_name"


def test_alias_counts_as_exact_match():
    result = match_vendor(_inv(supplier_name="ACME"), VENDORS)
    assert result.vendor_id == ACME_ID
    assert result.method == "exact_name"


def test_duplicate_vendor_names_are_flagged_not_auto_picked():
    # Two distinct vendors sharing a legal name (branches, re-registrations --
    # this happens) must never be silently resolved to either one.
    dup_a, dup_b = uuid.uuid4(), uuid.uuid4()
    dupes = (
        VendorIdentity(vendor_id=dup_a, legal_name="Global Traders Ltd", tax_id="IT111"),
        VendorIdentity(vendor_id=dup_b, legal_name="Global Traders Ltd", tax_id="IT222"),
    )
    result = match_vendor(_inv(supplier_name="Global Traders Ltd"), dupes)
    assert result.vendor_id is None
    assert result.method == "ambiguous"
    assert len(result.candidates) == 2


def test_no_identifying_info_yields_none():
    result = match_vendor(_inv(), VENDORS)
    assert result.vendor_id is None
    assert result.method == "none"
    assert result.confidence == 0.0


def test_unknown_vendor_below_floor_yields_none():
    result = match_vendor(_inv(supplier_name="Totally Different Co"), VENDORS)
    assert result.vendor_id is None
    assert result.method == "none"


PO_A = PoIdentity(po_id=uuid.uuid4(), po_number="PO-5567")
PO_B = PoIdentity(po_id=uuid.uuid4(), po_number="PO-9001")


def test_po_exact_match_ignores_formatting():
    result = match_po(_inv(purchase_order_number="po 5567"), (PO_A, PO_B))
    assert result.po_id == PO_A.po_id
    assert result.method == "exact_number"


def test_po_not_provided():
    result = match_po(_inv(), (PO_A, PO_B))
    assert result.po_id is None
    assert result.method == "not_provided"


def test_po_not_found():
    result = match_po(_inv(purchase_order_number="PO-0000"), (PO_A, PO_B))
    assert result.po_id is None
    assert result.method == "not_found"
