"""Unit tests for stock tools."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, call

import pytest


@pytest.fixture
def stock_write_models(
    mock_stock_item_class: MagicMock,
    mock_part_class: MagicMock,
    mock_stock_location_class: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    """Mock host hooks, not host validation behavior."""
    from django.db import transaction

    part = mock_part_class.objects.get.return_value
    part.pk = 1
    part.name = "Resistor"
    part.trackable = False
    part.default_location = None
    part.default_expiry = 0
    item = MagicMock()
    item.pk = 10
    item.part = part
    item.part_id = 1
    item.quantity = Decimal("2.50000")
    item.location = None
    item.location_id = None
    item.serial = ""
    item.batch = "B001"
    item.status = 10
    item.notes = "original"
    item.packaging = "reel"
    item.link = "https://example.com"
    item.expiry_date = date(2026, 12, 1)
    item.updated = datetime(2026, 10, 3)
    item.delete_on_deplete = False
    item.set_status.return_value = True
    mock_stock_item_class.objects.get.return_value = item
    mock_stock_item_class.objects.values_list.return_value.get.return_value = 1

    def construct(**kwargs: object) -> MagicMock:
        for key, value in kwargs.items():
            setattr(item, key, value)
        return item

    mock_stock_item_class.side_effect = construct
    monkeypatch.setattr("InvenTree.helpers.current_date", MagicMock(return_value=date(2026, 10, 3)))
    return mock_stock_item_class, mock_part_class, mock_stock_location_class, item, transaction.atomic


class TestCreateStockItem:
    @pytest.mark.parametrize("quantity", [0, 1, 2.5, "123456789.12345"])
    async def test_mcp_argument_validation_preserves_quantity(
        self, stock_write_models: tuple, quantity: float | str
    ) -> None:
        from inventree_mcp_plugin.mcp_server import mcp

        stock, _, _, _, _ = stock_write_models
        result = await mcp._tool_manager.call_tool("create_stock_item", {"part_id": 1, "quantity": quantity})
        assert stock.call_args.kwargs["quantity"] == Decimal(str(quantity))
        assert Decimal(result["quantity_decimal"]) == Decimal(str(quantity))

    @pytest.mark.parametrize("quantity", [True, False])
    async def test_mcp_rejects_boolean_quantity(self, stock_write_models: tuple, quantity: bool) -> None:
        from mcp.server.fastmcp.exceptions import ToolError

        from inventree_mcp_plugin.mcp_server import mcp

        stock, part, _, _, atomic = stock_write_models
        with pytest.raises(ToolError):
            await mcp._tool_manager.call_tool("create_stock_item", {"part_id": 1, "quantity": quantity})
        stock.assert_not_called()
        part.objects.select_for_update.assert_not_called()
        atomic.assert_not_called()

    @pytest.mark.parametrize("quantity", [0, 2.5, "0", "2.50000", "123456789.12345"])
    async def test_quantity_and_hooks(self, stock_write_models: tuple, quantity: float | str) -> None:
        from inventree_mcp_plugin.tools.simple.stock import create_stock_item

        stock, part, _, item, atomic = stock_write_models
        result = await create_stock_item(1, quantity)
        assert stock.call_args.kwargs["quantity"] == Decimal(str(quantity))
        assert "batch" not in stock.call_args.kwargs
        assert "status" not in stock.call_args.kwargs
        assert item.method_calls == [call.full_clean(), call.save(user=None), call.refresh_from_db()]
        part.objects.select_for_update.assert_called_once_with()
        part.objects.get.assert_called_once_with(pk=1)
        atomic.return_value.__enter__.assert_called_once()
        atomic.return_value.__exit__.assert_called_once_with(None, None, None)
        assert result["quantity_decimal"] == str(Decimal(str(quantity)))
        assert result["quantity"] == float(quantity)
        json.dumps(result)

    @pytest.mark.parametrize("quantity", [True, False, "bad", "", "NaN", "sNaN", "Infinity", "-Infinity", "-1", -0.5])
    async def test_invalid_quantity_before_database(self, stock_write_models: tuple, quantity: float | str) -> None:
        from inventree_mcp_plugin.tools.simple.stock import create_stock_item

        stock, part, _, _, atomic = stock_write_models
        with pytest.raises(ValueError, match="finite nonnegative"):
            await create_stock_item(1, quantity)
        stock.assert_not_called()
        part.objects.select_for_update.assert_not_called()
        atomic.assert_not_called()

    async def test_defaults_and_explicit_blank_batch(self, stock_write_models: tuple) -> None:
        from inventree_mcp_plugin.tools.simple.stock import create_stock_item

        stock, part, locations, _, _ = stock_write_models
        part.objects.get.return_value.default_location = locations.objects.get.return_value
        part.objects.get.return_value.default_expiry = 10
        await create_stock_item(1, "0", batch="")
        assert stock.call_args.kwargs["location"] is locations.objects.get.return_value
        assert stock.call_args.kwargs["expiry_date"] == date(2026, 10, 13)
        assert stock.call_args.kwargs["batch"] == ""
        locations.objects.get.assert_not_called()

    async def test_explicit_values_and_single_serial(self, stock_write_models: tuple) -> None:
        from inventree_mcp_plugin.tools.simple.stock import create_stock_item

        stock, part, locations, item, _ = stock_write_models
        part.objects.get.return_value.trackable = True
        part.objects.get.return_value.default_expiry = 10
        result = await create_stock_item(
            1,
            "1.00000",
            location_id=5,
            serial=" SN-1 ",
            batch="batch",
            status=20,
            expiry_date="2027-01-02",
            notes="metadata",
            packaging="box",
            link="https://example.com",
        )
        assert stock.call_args.kwargs["serial"] == "SN-1"
        assert stock.call_args.kwargs["expiry_date"] == date(2027, 1, 2)
        locations.objects.get.assert_called_once_with(pk=5)
        assert item.method_calls == [
            call.set_status(20),
            call.full_clean(),
            call.save(user=None),
            call.refresh_from_db(),
        ]
        assert result["notes"] == "metadata"

    @pytest.mark.parametrize("quantity", ["0", "2", "0.5"])
    async def test_serial_quantity_not_silently_normalized(self, stock_write_models: tuple, quantity: str) -> None:
        from inventree_mcp_plugin.tools.simple.stock import create_stock_item

        stock, part, _, item, _ = stock_write_models
        part.objects.get.return_value.trackable = True
        with pytest.raises(ValueError, match="exactly 1"):
            await create_stock_item(1, quantity, serial="SN")
        stock.assert_not_called()
        item.full_clean.assert_not_called()

    @pytest.mark.parametrize(
        "trackable,delete_on_deplete,message", [(False, False, "trackable"), (True, True, "deplete")]
    )
    async def test_serial_constraints(
        self, stock_write_models: tuple, trackable: bool, delete_on_deplete: bool, message: str
    ) -> None:
        from inventree_mcp_plugin.tools.simple.stock import create_stock_item

        stock, part, _, _, _ = stock_write_models
        part.objects.get.return_value.trackable = trackable
        with pytest.raises(ValueError, match=message):
            await create_stock_item(1, 1, serial="SN", delete_on_deplete=delete_on_deplete)
        stock.assert_not_called()


class TestUpdateStockItem:
    async def test_preserves_lifecycle_and_omitted_metadata(self, stock_write_models: tuple) -> None:
        from inventree_mcp_plugin.tools.simple.stock import update_stock_item

        stock, part, _, item, atomic = stock_write_models
        events = MagicMock()
        events.attach_mock(stock.objects.values_list, "initial_part")
        events.attach_mock(part.objects.select_for_update, "part_lock")
        events.attach_mock(part.objects.get, "part_get")
        events.attach_mock(stock.objects.select_for_update, "item_lock")
        events.attach_mock(stock.objects.get, "item_get")
        result = await update_stock_item(10, notes="")
        assert events.mock_calls == [
            call.initial_part("part_id", flat=True),
            call.initial_part().get(pk=10),
            call.part_lock(),
            call.part_get(pk=1),
            call.item_lock(),
            call.item_get(pk=10, part_id=1),
            call.item_get().full_clean(),
            call.item_get().save(user=None),
            call.item_get().refresh_from_db(),
        ]
        stock.objects.only.assert_not_called()
        assert item.method_calls == [call.full_clean(), call.save(user=None), call.refresh_from_db()]
        assert result["notes"] == ""
        assert result["quantity_decimal"] == "2.50000"
        assert result["batch"] == "B001"
        assert result["expiry_date"] == "2026-12-01"
        assert result["location"] is None
        assert result["part"] == 1
        item.move.assert_not_called()
        item.add_stock.assert_not_called()
        item.take_stock.assert_not_called()
        atomic.return_value.__enter__.assert_called_once()
        json.dumps(result)

    async def test_clear_text_and_expiry(self, stock_write_models: tuple) -> None:
        from inventree_mcp_plugin.tools.simple.stock import update_stock_item

        _, _, _, item, _ = stock_write_models
        result = await update_stock_item(10, serial=" ", batch="", packaging="", link="", clear_expiry_date=True)
        assert result["serial"] == result["batch"] == result["packaging"] == result["link"] == ""
        assert result["expiry_date"] is None
        assert item.quantity == Decimal("2.50000")

    async def test_explicit_date_and_false_flag(self, stock_write_models: tuple) -> None:
        from inventree_mcp_plugin.tools.simple.stock import update_stock_item

        result = await update_stock_item(10, expiry_date="2027-02-01", delete_on_deplete=False)
        assert result["expiry_date"] == "2027-02-01"
        assert result["delete_on_deplete"] is False

    @pytest.mark.parametrize(
        "kwargs", [{}, {"clear_expiry_date": False}, {"expiry_date": "2027-01-01", "clear_expiry_date": True}]
    )
    async def test_empty_or_conflicting_update_before_db(self, stock_write_models: tuple, kwargs: dict) -> None:
        from inventree_mcp_plugin.tools.simple.stock import update_stock_item

        stock, _, _, _, atomic = stock_write_models
        with pytest.raises(ValueError):
            await update_stock_item(10, **kwargs)
        stock.objects.values_list.assert_not_called()
        atomic.assert_not_called()

    @pytest.mark.parametrize("quantity", ["0", "2", "0.5"])
    @pytest.mark.parametrize("existing_serial", [True, False])
    async def test_serial_quantity_guard_on_metadata_edits(
        self, stock_write_models: tuple, quantity: str, existing_serial: bool
    ) -> None:
        from inventree_mcp_plugin.tools.simple.stock import update_stock_item

        _, part, _, item, _ = stock_write_models
        part.objects.get.return_value.trackable = True
        item.quantity = Decimal(quantity)
        item.serial = "SN" if existing_serial else ""
        with pytest.raises(ValueError, match="exactly 1"):
            await update_stock_item(10, notes="changed", serial=None if existing_serial else " SN ")
        assert item.notes == "original"
        item.full_clean.assert_not_called()
        item.save.assert_not_called()

    @pytest.mark.parametrize(
        "trackable,delete_on_deplete,message", [(False, None, "trackable"), (True, True, "deplete")]
    )
    async def test_serial_constraints(
        self, stock_write_models: tuple, trackable: bool, delete_on_deplete: bool | None, message: str
    ) -> None:
        from inventree_mcp_plugin.tools.simple.stock import update_stock_item

        _, part, _, item, _ = stock_write_models
        part.objects.get.return_value.trackable = trackable
        item.quantity = Decimal(1)
        with pytest.raises(ValueError, match=message):
            await update_stock_item(10, serial="SN", delete_on_deplete=delete_on_deplete)
        item.full_clean.assert_not_called()

    async def test_successful_serial_and_status(self, stock_write_models: tuple) -> None:
        from inventree_mcp_plugin.tools.simple.stock import update_stock_item

        _, part, _, item, _ = stock_write_models
        part.objects.get.return_value.trackable = True
        item.quantity = Decimal(1)
        result = await update_stock_item(10, serial=" SN ", status=20)
        assert result["serial"] == "SN"
        assert item.method_calls == [
            call.set_status(20),
            call.full_clean(),
            call.save(user=None),
            call.refresh_from_db(),
        ]


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize(
    "value", ["20261003", "2026-W40-6", "2026-1-01", "2026-02-30", "", "2026-10-03T00:00:00", " 2026-10-03"]
)
async def test_strict_expiry_before_database(stock_write_models: tuple, operation: str, value: str) -> None:
    from inventree_mcp_plugin.tools.simple.stock import create_stock_item, update_stock_item

    stock, part, _, _, atomic = stock_write_models
    with pytest.raises(ValueError):
        if operation == "create":
            await create_stock_item(1, 1, expiry_date=value)
        else:
            await update_stock_item(10, expiry_date=value)
    stock.assert_not_called()
    part.objects.select_for_update.assert_not_called()
    atomic.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_invalid_status_no_validation_or_save(stock_write_models: tuple, operation: str) -> None:
    from inventree_mcp_plugin.tools.simple.stock import create_stock_item, update_stock_item

    _, _, _, item, atomic = stock_write_models
    item.set_status.return_value = False
    with pytest.raises(ValueError, match="Invalid stock status"):
        if operation == "create":
            await create_stock_item(1, 1, status=999)
        else:
            await update_stock_item(10, status=999, notes="changed", batch="changed", clear_expiry_date=True)
            # Transaction mocks do not implement database rollback.
    item.full_clean.assert_not_called()
    item.save.assert_not_called()
    exit_args = atomic.return_value.__exit__.call_args.args
    assert exit_args[0] is ValueError
    if operation == "update":
        assert item.notes == "original"
        assert item.batch == "B001"
        assert item.expiry_date == date(2026, 12, 1)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize(
    "message",
    [
        "duplicate serial",
        "virtual part",
        "structural location",
        "fractional trackable",
        "decimal precision",
        "invalid URL",
        "custom batch",
    ],
)
async def test_host_validation_propagated_without_save(stock_write_models: tuple, operation: str, message: str) -> None:
    from inventree_mcp_plugin.tools.simple.stock import create_stock_item, update_stock_item

    _, _, _, item, atomic = stock_write_models
    error = ValueError(message)
    item.full_clean.side_effect = error
    with pytest.raises(ValueError) as exc:
        if operation == "create":
            await create_stock_item(1, "0.123456")
        else:
            await update_stock_item(10, batch="invalid")
    assert exc.value is error
    item.save.assert_not_called()
    item.refresh_from_db.assert_not_called()
    assert atomic.return_value.__exit__.call_args.args[1] is error


@pytest.mark.parametrize("target", ["part", "location", "item"])
async def test_missing_foreign_keys(stock_write_models: tuple, target: str) -> None:
    from inventree_mcp_plugin.tools.simple.stock import create_stock_item, update_stock_item

    stock, part, locations, item, _ = stock_write_models
    error = LookupError("missing")
    model = {"part": part, "location": locations, "item": stock}[target]
    model.objects.get.side_effect = error
    with pytest.raises(LookupError) as exc:
        if target == "item":
            await update_stock_item(10, notes="changed")
        else:
            await create_stock_item(1, 1, location_id=5)
    assert exc.value is error
    item.save.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_hooks_inside_atomic_and_response_after_refresh(stock_write_models: tuple, operation: str) -> None:
    from inventree_mcp_plugin.tools.simple.stock import create_stock_item, update_stock_item

    _, _, _, item, atomic = stock_write_models
    active = False

    def enter() -> None:
        nonlocal active
        active = True

    def exit_atomic(*args: object) -> None:
        nonlocal active
        active = False

    def validate() -> None:
        assert active

    def save(*, user: object) -> None:
        assert active
        assert user is None

    def refresh() -> None:
        assert active
        item.quantity = Decimal("1.00000")
        item.notes = "refreshed"

    atomic.return_value.__enter__.side_effect = enter
    atomic.return_value.__exit__.side_effect = exit_atomic
    item.full_clean.side_effect = validate
    item.save.side_effect = save
    item.refresh_from_db.side_effect = refresh
    if operation == "create":
        result = await create_stock_item(1, 1)
    else:
        result = await update_stock_item(10, notes="changed")
    assert not active
    assert result["quantity_decimal"] == "1.00000"
    assert result["notes"] == "refreshed"


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_integrity_errors_not_masked(stock_write_models: tuple, operation: str) -> None:
    from django.db.utils import IntegrityError

    from inventree_mcp_plugin.tools.simple.stock import create_stock_item, update_stock_item

    _, _, _, item, atomic = stock_write_models
    error = IntegrityError("database conflict")
    item.save.side_effect = error
    with pytest.raises(IntegrityError) as exc:
        if operation == "create":
            await create_stock_item(1, 1)
        else:
            await update_stock_item(10, notes="changed")
    assert exc.value is error
    item.refresh_from_db.assert_not_called()
    assert atomic.return_value.__exit__.call_args.args[1] is error


class TestGetStockItem:
    async def test_all_old_and_new_fields(self, stock_write_models: tuple) -> None:
        from inventree_mcp_plugin.tools.simple.stock import get_stock_item

        result = await get_stock_item(10)
        assert result == {
            "id": 10,
            "part": 1,
            "part_name": "Resistor",
            "quantity": 2.5,
            "quantity_decimal": "2.50000",
            "location": None,
            "location_name": None,
            "serial": "",
            "batch": "B001",
            "status": 10,
            "notes": "original",
            "updated": "2026-10-03 00:00:00",
            "packaging": "reel",
            "link": "https://example.com",
            "expiry_date": "2026-12-01",
            "delete_on_deplete": False,
        }
        json.dumps(result)

    async def test_projection(self, stock_write_models: tuple) -> None:
        from inventree_mcp_plugin.tools.simple.stock import get_stock_item

        stock, _, _, _, _ = stock_write_models
        result = await get_stock_item(10, fields=["quantity_decimal", "expiry_date", "unknown"])
        assert result == {"id": 10, "quantity_decimal": "2.50000", "expiry_date": "2026-12-01"}
        assert set(stock.objects.only.call_args.args) == {"pk", "quantity", "expiry_date"}
        stock.objects.select_related.assert_not_called()


class TestListStockItems:
    async def test_list_stock_items(self, mock_stock_item_class: MagicMock) -> None:
        mock_item = MagicMock()
        mock_item.pk = 10
        mock_item.part_id = 1
        mock_item.part.name = "Resistor"
        mock_item.quantity = 100
        mock_item.location_id = 5
        mock_item.serial = None
        mock_item.batch = "B001"

        mock_stock_item_class.objects.all.return_value.order_by.return_value.__getitem__ = MagicMock(
            return_value=[mock_item]
        )

        from inventree_mcp_plugin.tools.simple.stock import list_stock_items

        result = await list_stock_items()
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 10
        assert result[0]["quantity"] == 100.0
