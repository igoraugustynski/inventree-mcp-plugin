"""Stock tools: list, get, create, edit, adjust, transfer."""

from __future__ import annotations

# Prevent InvenTree's plugin scanner from picking up the `mcp` FastMCP instance.
__all__: list[str] = []

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, cast

from pydantic import StrictFloat  # noqa: TC002 - FastMCP evaluates tool annotations at runtime.

if TYPE_CHECKING:
    from stock.models import StockItem

from ...mcp_server import mcp
from ...tools import django_orm

# Direct ORM columns used by the stock list projection.
_STOCK_COLS: dict[str, str] = {
    "part": "part_id",
    "quantity": "quantity",
    "location": "location_id",
    "serial": "serial",
    "batch": "batch",
}

_DETAIL_COLS = {
    **_STOCK_COLS,
    "quantity_decimal": "quantity",
    "status": "status",
    "notes": "notes",
    "updated": "updated",
    "packaging": "packaging",
    "link": "link",
    "expiry_date": "expiry_date",
    "delete_on_deplete": "delete_on_deplete",
}


def _stock_row(item: StockItem, want: set[str] | None = None) -> dict[str, Any]:
    """Serialize stock details without loading unrequested deferred fields."""
    if want is None:
        want = set(_DETAIL_COLS) | {"part_name", "location_name"}
    row: dict[str, Any] = {"id": item.pk}
    for field in want & _DETAIL_COLS.keys():
        value = getattr(item, _DETAIL_COLS[field])
        if field == "quantity":
            value = float(value)
        elif field == "quantity_decimal":
            value = str(value)
        elif field == "notes":
            value = value or ""
        elif field == "updated":
            value = str(value) if value else None
        elif field == "expiry_date":
            value = value.isoformat() if value else None
        row[field] = value
    if "part_name" in want:
        row["part_name"] = item.part.name
    if "location_name" in want:
        row["location_name"] = item.location.name if item.location else None
    return row


def _quantity(value: float | str) -> Decimal:
    try:
        quantity = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Quantity must be a finite nonnegative decimal") from exc
    if isinstance(value, bool) or not quantity.is_finite() or quantity < 0:
        raise ValueError("Quantity must be a finite nonnegative decimal")
    return quantity


def _expiry_date(value: str | None) -> date | None:
    if value is None:
        return None
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Expiry date must use YYYY-MM-DD")
    return parsed


def _validate_serial(serial: str, quantity: Decimal, trackable: bool, delete_on_deplete: bool | None) -> None:
    if serial:
        if not trackable:
            raise ValueError("Serial numbers require a trackable part")
        if quantity != Decimal(1):
            raise ValueError("Serialized stock must have quantity exactly 1")
        if delete_on_deplete is True:
            raise ValueError("Serialized stock cannot use delete_on_deplete")


@mcp.tool()
@django_orm
def create_stock_item(
    part_id: int,
    quantity: StrictFloat | str,
    location_id: int | None = None,
    serial: str | None = None,
    batch: str | None = None,
    status: int | None = None,
    notes: str = "",
    packaging: str = "",
    link: str = "",
    expiry_date: str | None = None,
    delete_on_deplete: bool = False,
) -> dict[str, Any]:
    """Create one stock item in raw units, without pack-size conversion or serial expansion.

    Args:
        part_id: Existing part ID.
        quantity: Finite nonnegative quantity; use a decimal string for exact precision.
        location_id: Existing location ID, or the part's default location when omitted.
        serial: Single serial number; requires a trackable part and quantity exactly 1.
        batch: Batch code. Omit to use the host generator; an empty string stays blank.
        status: Host stock status code; omitted uses the model default.
        notes: Stock item notes.
        packaging: Packaging description.
        link: External URL.
        expiry_date: YYYY-MM-DD, or the part's default expiry when omitted.
        delete_on_deplete: Delete depleted stock; cannot be true for serialized stock.
    """
    from django.db import transaction
    from InvenTree.helpers import current_date
    from part.models import Part
    from stock.models import StockItem, StockLocation

    amount = _quantity(quantity)
    expiry = _expiry_date(expiry_date)
    serial = serial.strip() if serial is not None else ""
    with transaction.atomic():
        # Exact-part plugin writers share this lock; REST and variant writers do not.
        part = cast("Any", Part.objects).select_for_update().get(pk=part_id)
        _validate_serial(serial, amount, part.trackable, delete_on_deplete)
        location = StockLocation.objects.get(pk=location_id) if location_id is not None else part.default_location
        if expiry_date is None and part.default_expiry > 0:
            expiry = current_date() + timedelta(days=part.default_expiry)
        kwargs: dict[str, Any] = {
            "part": part,
            "quantity": amount,
            "location": location,
            "serial": serial,
            "notes": notes,
            "packaging": packaging,
            "link": link,
            "expiry_date": expiry,
            "delete_on_deplete": delete_on_deplete,
        }
        if batch is not None:
            kwargs["batch"] = batch
        item = StockItem(**kwargs)
        if status is not None and not item.set_status(status):
            raise ValueError("Invalid stock status")
        item.full_clean()
        item.save(user=None)
        item.refresh_from_db()
        return _stock_row(item)


@mcp.tool()
@django_orm
def update_stock_item(
    stock_item_id: int,
    serial: str | None = None,
    batch: str | None = None,
    status: int | None = None,
    notes: str | None = None,
    packaging: str | None = None,
    link: str | None = None,
    expiry_date: str | None = None,
    clear_expiry_date: bool = False,
    delete_on_deplete: bool | None = None,
) -> dict[str, Any]:
    """Edit stock metadata only; quantity, location, part and provenance are unchanged.

    Omitted values are preserved. Empty strings clear text fields, subject to host
    serial rules. Host validation and tracking hooks run on every save.

    Args:
        stock_item_id: Existing stock item ID.
        serial: Single serial number; requires a trackable part and quantity exactly 1.
        batch: Batch code.
        status: Host stock status code.
        notes: Stock item notes (not a tracking-entry note).
        packaging: Packaging description.
        link: External URL.
        expiry_date: Expiry date in YYYY-MM-DD format.
        clear_expiry_date: Clear expiry; cannot be combined with expiry_date.
        delete_on_deplete: Delete depleted stock; cannot be true for serialized stock.
    """
    from django.db import transaction
    from part.models import Part
    from stock.models import StockItem

    if clear_expiry_date and expiry_date is not None:
        raise ValueError("Cannot set and clear expiry date together")
    expiry = _expiry_date(expiry_date)
    changes = {
        "serial": serial.strip() if serial is not None else None,
        "batch": batch,
        "notes": notes,
        "packaging": packaging,
        "link": link,
        "delete_on_deplete": delete_on_deplete,
    }
    changes = {key: value for key, value in changes.items() if value is not None}
    if not changes and status is None and expiry_date is None and not clear_expiry_date:
        raise ValueError("At least one stock metadata field must be provided")
    with transaction.atomic():
        part_id = StockItem.objects.values_list("part_id", flat=True).get(pk=stock_item_id)
        part = cast("Any", Part.objects).select_for_update().get(pk=part_id)
        # Full instance: deferred fields are unsafe for host validation / tree saves.
        item = StockItem.objects.select_for_update().get(pk=stock_item_id, part_id=part_id)
        effective_serial = cast("str", changes.get("serial", item.serial or "")).strip()
        _validate_serial(effective_serial, Decimal(str(item.quantity)), part.trackable, delete_on_deplete)
        if status is not None and not item.set_status(status):
            raise ValueError("Invalid stock status")
        for key, value in changes.items():
            setattr(item, key, value)
        if expiry_date is not None or clear_expiry_date:
            item.expiry_date = expiry
        item.full_clean()
        item.save(user=None)
        item.refresh_from_db()
        return _stock_row(item)


@mcp.tool()
@django_orm
def list_stock_items(
    part_id: int | None = None,
    location_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List stock items with optional filtering.

    Args:
        part_id: Filter by part ID.
        location_id: Filter by stock location ID.
        limit: Maximum number of results.
        offset: Number of results to skip.
        fields: Fields to include. Available: id, part, part_name, quantity, location,
                serial, batch. Defaults to all. ``id`` is always included.
    """
    from stock.models import StockItem

    want = set(fields) if fields is not None else set(_STOCK_COLS) | {"id", "part_name"}

    queryset = StockItem.objects.all()
    if part_id is not None:
        queryset = queryset.filter(part_id=part_id)
    if location_id is not None:
        queryset = queryset.filter(location_id=location_id)

    orm_cols = {"pk"} | {_STOCK_COLS[f] for f in want if f in _STOCK_COLS}
    want_part_name = "part_name" in want
    if want_part_name:
        orm_cols.add("part_id")
        queryset = queryset.select_related("part").only(*orm_cols, "part__name")
    else:
        queryset = queryset.only(*orm_cols)

    items = queryset.order_by("pk")[offset : offset + limit]
    results: list[dict[str, Any]] = []
    for item in items:
        row: dict[str, Any] = {"id": item.pk}
        if "part" in want:
            row["part"] = item.part_id
        if want_part_name:
            row["part_name"] = item.part.name
        if "quantity" in want:
            row["quantity"] = float(item.quantity)
        if "location" in want:
            row["location"] = item.location_id
        if "serial" in want:
            row["serial"] = item.serial
        if "batch" in want:
            row["batch"] = item.batch
        results.append(row)
    return results


@mcp.tool()
@django_orm
def get_stock_item(
    stock_item_id: int,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Get detailed information about a specific stock item.

    Args:
        stock_item_id: The ID of the stock item.
        fields: Fields to include. Available: id, part, part_name, quantity, location,
                location_name, serial, batch, status, notes, updated, packaging, link,
                expiry_date, delete_on_deplete, quantity_decimal. Defaults to all.
                ``id`` is always included.
    """
    from stock.models import StockItem

    detail_cols = _DETAIL_COLS
    want = set(fields) if fields is not None else set(detail_cols) | {"id", "part_name", "location_name"}

    want_part_name = "part_name" in want
    want_location_name = "location_name" in want

    orm_cols = {"pk"} | {detail_cols[f] for f in want if f in detail_cols}
    if want_part_name:
        orm_cols.add("part_id")
    if want_location_name:
        orm_cols.add("location_id")

    queryset = StockItem.objects.only(*orm_cols)
    if want_part_name:
        queryset = queryset.select_related("part")
    if want_location_name:
        queryset = queryset.select_related("location")

    item = queryset.get(pk=stock_item_id)
    return _stock_row(item, want)


@mcp.tool()
@django_orm
def adjust_stock(stock_item_id: int, quantity: float, notes: str = "") -> dict[str, Any]:
    """Adjust the quantity of a stock item (add or remove stock).

    Args:
        stock_item_id: The ID of the stock item.
        quantity: Quantity to adjust by (positive to add, negative to remove).
        notes: Notes for the stock adjustment.
    """
    from decimal import Decimal

    from stock.models import StockItem

    item = StockItem.objects.get(pk=stock_item_id)
    if quantity > 0:
        item.add_stock(Decimal(str(quantity)), None, notes=notes)
    elif quantity < 0:
        item.take_stock(Decimal(str(abs(quantity))), None, notes=notes)

    item.refresh_from_db()
    return {
        "id": item.pk,
        "quantity": float(item.quantity),
        "notes": notes,
    }


@mcp.tool()
@django_orm
def transfer_stock(stock_item_id: int, location_id: int, notes: str = "") -> dict[str, Any]:
    """Transfer a stock item to a different location.

    Args:
        stock_item_id: The ID of the stock item to transfer.
        location_id: The destination location ID.
        notes: Notes for the transfer.
    """
    from stock.models import StockItem, StockLocation

    item = StockItem.objects.get(pk=stock_item_id)
    location = StockLocation.objects.get(pk=location_id)
    item.move(location, notes=notes, user=None)
    item.refresh_from_db()
    return {
        "id": item.pk,
        "quantity": float(item.quantity),
        "location": item.location_id,
        "location_name": item.location.name if item.location else None,
    }
