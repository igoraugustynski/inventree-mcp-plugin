"""Mocked tests for generic parameter tools and their Part-only scope."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest


def _template() -> MagicMock:
    template = MagicMock()
    template.pk = 3
    template.name = "Resistance"
    template.description = "Nominal resistance"
    template.units = "ohm"
    template.get_choices.return_value = ["10", "20"]
    template.checkbox = False
    template.enabled = True
    template.model_type_id = None
    template.choices = "10,20"
    template.selectionlist_id = None
    template.unique = 0
    return template


def _parameter() -> MagicMock:
    parameter = MagicMock()
    parameter.pk = 7
    parameter.model_id = 42
    parameter.template_id = 3
    parameter.template = _template()
    parameter.data = "10"
    parameter.data_numeric = 10.0
    parameter.note = "Original note"
    return parameter


def _assert_part_scope(mock_parameter_class: MagicMock) -> None:
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part

    ContentType.objects.get_for_model.assert_called_once_with(Part)
    mock_parameter_class.objects.filter.assert_called_once_with(
        model_type=ContentType.objects.get_for_model.return_value
    )


async def test_list_templates_search_paging_projection(mock_parameter_template_class: MagicMock) -> None:
    from django.db.models import Q

    from inventree_mcp_plugin.tools.simple.parameters import list_parameter_templates

    qs = mock_parameter_template_class.objects.all.return_value
    qs.__getitem__.return_value = [_template()]
    result = await list_parameter_templates(search="res", limit=2, offset=4, fields=["choices", "unknown"])
    assert result == [{"id": 3, "choices": ["10", "20"]}]
    from django.contrib.contenttypes.models import ContentType

    assert Q.call_args_list == [
        call(model_type__isnull=True),
        call(model_type=ContentType.objects.get_for_model.return_value),
        call(name__icontains="res"),
        call(description__icontains="res"),
    ]
    assert mock_parameter_template_class.objects.filter.call_args.kwargs == {}
    assert qs.filter.call_args_list[0] == call(enabled=True)
    assert qs.filter.call_count == 2
    qs.select_related.assert_called_once_with("selectionlist")
    qs.order_by.assert_called_once_with("pk")
    qs.__getitem__.assert_called_once_with(slice(4, 6))


async def test_get_template_all_fields(mock_parameter_template_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import get_parameter_template

    mock_parameter_template_class.objects.get.return_value = _template()
    assert await get_parameter_template(3) == {
        "id": 3,
        "name": "Resistance",
        "description": "Nominal resistance",
        "units": "ohm",
        "choices": ["10", "20"],
        "checkbox": False,
        "enabled": True,
        "model_type": None,
        "choices_raw": "10,20",
        "selection_list": None,
    }
    mock_parameter_template_class.objects.get.assert_called_once_with(pk=3)
    assert await get_parameter_template(3, fields=[]) == {"id": 3}


@pytest.mark.parametrize("search", [None, ""])
async def test_list_templates_without_search(search: str | None, mock_parameter_template_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import list_parameter_templates

    qs = mock_parameter_template_class.objects.all.return_value
    qs.__getitem__.return_value = []
    assert await list_parameter_templates(search=search) == []
    qs.filter.assert_called_once_with(enabled=True)
    qs.__getitem__.assert_called_once_with(slice(0, 100))


async def test_list_part_parameters_scope_filter_paging(
    mock_parameter_class: MagicMock, mock_part_class: MagicMock
) -> None:
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part

    from inventree_mcp_plugin.tools.simple.parameters import list_part_parameters

    mock_part_class.objects.get.return_value.pk = 42
    qs = mock_parameter_class.objects.all.return_value
    qs.__getitem__.return_value = [_parameter()]
    result = await list_part_parameters(42, template_id=3, limit=2, offset=1, fields=["part", "data", "unknown"])
    assert result == [{"id": 7, "part": 42, "data": "10"}]
    mock_part_class.objects.get.assert_called_once_with(pk=42)
    ContentType.objects.get_for_model.assert_called_once_with(Part)
    mock_parameter_class.objects.filter.assert_called_once_with(
        model_type=ContentType.objects.get_for_model.return_value, model_id=42
    )
    qs.filter.assert_called_once_with(template_id=3)
    qs.select_related.assert_called_once_with("template")
    qs.order_by.assert_called_once_with("pk")
    qs.__getitem__.assert_called_once_with(slice(1, 3))


async def test_list_empty_and_no_template_filter(mock_parameter_class: MagicMock, mock_part_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import list_part_parameters

    qs = mock_parameter_class.objects.all.return_value
    qs.__getitem__.return_value = []
    assert await list_part_parameters(42, limit=0) == []
    qs.filter.assert_not_called()
    qs.__getitem__.assert_called_once_with(slice(0, 0))


@pytest.mark.parametrize("tool", ["list_parameter_templates", "list_part_parameters"])
@pytest.mark.parametrize("kwargs", [{"limit": -1}, {"offset": -1}])
async def test_negative_paging_rejected(tool: str, kwargs: dict, mock_parameter_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple import parameters

    args = (42,) if tool == "list_part_parameters" else ()
    with pytest.raises(ValueError, match="nonnegative"):
        await getattr(parameters, tool)(*args, **kwargs)
    mock_parameter_class.objects.filter.assert_not_called()


async def test_get_part_parameter_scoped_and_serialized(mock_parameter_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import get_part_parameter

    mock_parameter_class.objects.get.return_value = _parameter()
    assert await get_part_parameter(7) == {
        "id": 7,
        "part": 42,
        "template": 3,
        "name": "Resistance",
        "units": "ohm",
        "data": "10",
        "data_numeric": 10.0,
        "note": "Original note",
    }
    _assert_part_scope(mock_parameter_class)
    mock_parameter_class.objects.get.assert_called_once_with(pk=7)


async def test_get_part_parameter_projection_and_null_numeric(mock_parameter_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import get_part_parameter

    parameter = _parameter()
    parameter.data_numeric = None
    mock_parameter_class.objects.get.return_value = parameter
    assert await get_part_parameter(7, fields=["data_numeric", "unknown"]) == {"id": 7, "data_numeric": None}


async def test_create_validates_and_saves_in_transaction(
    mock_parameter_class: MagicMock, mock_parameter_template_class: MagicMock, mock_part_class: MagicMock
) -> None:
    from django.contrib.contenttypes.models import ContentType
    from django.db import transaction
    from part.models import Part

    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter

    mock_part_class.objects.get.return_value.pk = 42
    template = _template()
    mock_parameter_template_class.objects.get.return_value = template
    parameter = _parameter()
    mock_parameter_class.return_value = parameter
    result = await create_part_parameter(42, 3, "10", note="New note")
    assert result["id"] == 7
    mock_parameter_class.assert_called_once_with(
        model_type=ContentType.objects.get_for_model.return_value,
        model_id=42,
        template=template,
        data="10",
        note="New note",
    )
    ContentType.objects.get_for_model.assert_called_once_with(Part)
    mock_part_class.objects.get.assert_called_once_with(pk=42)
    mock_parameter_template_class.objects.get.assert_called_once_with(pk=3)
    assert parameter.mock_calls[:2] == [call.full_clean(), call.save()]
    transaction.atomic.assert_called_once_with()
    transaction.atomic.return_value.__enter__.assert_called_once()
    transaction.atomic.return_value.__exit__.assert_called_once_with(None, None, None)
    mock_parameter_class.objects.create.assert_not_called()
    mock_parameter_class.objects.update_or_create.assert_not_called()


async def test_create_default_note(mock_parameter_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter

    mock_parameter_class.return_value = _parameter()
    await create_part_parameter(42, 3, "10")
    assert mock_parameter_class.call_args.kwargs["note"] == ""


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_write_response_uses_model_computed_values(operation: str, mock_parameter_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter, update_part_parameter

    parameter = _parameter()
    mock_parameter_class.return_value = parameter
    mock_parameter_class.objects.get.return_value = parameter

    def save() -> None:
        parameter.data = "1000 ohm"
        parameter.data_numeric = 1000.0

    parameter.save.side_effect = save
    if operation == "create":
        result = await create_part_parameter(42, 3, "1k ohm")
    else:
        result = await update_part_parameter(7, data="1k ohm")
    assert result["data"] == "1000 ohm"
    assert result["data_numeric"] == 1000.0


@pytest.mark.parametrize("kwargs", [{"data": "20"}, {"note": ""}, {"data": "", "note": "Revised"}])
async def test_update_preserves_omitted_fields_and_locks(kwargs: dict, mock_parameter_class: MagicMock) -> None:
    from django.db import transaction

    from inventree_mcp_plugin.tools.simple.parameters import update_part_parameter

    parameter = _parameter()
    mock_parameter_class.objects.get.return_value = parameter
    result = await update_part_parameter(7, **kwargs)
    assert result["data"] == kwargs.get("data", "10")
    assert result["note"] == kwargs.get("note", "Original note")
    _assert_part_scope(mock_parameter_class)
    qs = mock_parameter_class.objects.all.return_value
    qs.select_for_update.assert_called_once_with()
    assert mock_parameter_class.objects.get.call_args_list == [call(pk=7), call(pk=7, template_id=3)]
    assert parameter.mock_calls[:2] == [call.full_clean(), call.save()]
    transaction.atomic.assert_called_once_with()
    transaction.atomic.return_value.__enter__.assert_called_once()
    transaction.atomic.return_value.__exit__.assert_called_once_with(None, None, None)
    qs.update.assert_not_called()


async def test_update_noop_rejected(mock_parameter_class: MagicMock) -> None:
    from django.db import transaction

    from inventree_mcp_plugin.tools.simple.parameters import update_part_parameter

    with pytest.raises(ValueError, match="At least one"):
        await update_part_parameter(7)
    transaction.atomic.assert_not_called()
    mock_parameter_class.objects.filter.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("message", ["Duplicate parameter", "Invalid choice", "Invalid units", "Plugin validation"])
async def test_validation_errors_propagate_without_save(
    operation: str, message: str, mock_parameter_class: MagicMock
) -> None:
    from django.db import transaction

    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter, update_part_parameter

    parameter = _parameter()
    error = ValueError(message)
    parameter.full_clean.side_effect = error
    mock_parameter_class.return_value = parameter
    mock_parameter_class.objects.get.return_value = parameter
    with pytest.raises(ValueError) as exc:
        if operation == "create":
            await create_part_parameter(42, 3, "bad")
        else:
            await update_part_parameter(7, data="bad")
    assert exc.value is error
    parameter.save.assert_not_called()
    assert transaction.atomic.return_value.__exit__.call_args.args[:2] == (ValueError, error)


@pytest.mark.parametrize("operation", ["get", "update"])
@pytest.mark.parametrize("message", ["Missing parameter", "Parameter belongs to another resource"])
async def test_parameter_lookup_errors_are_scoped(
    operation: str, message: str, mock_parameter_class: MagicMock
) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import get_part_parameter, update_part_parameter

    error = LookupError(message)
    mock_parameter_class.objects.get.side_effect = error
    with pytest.raises(LookupError) as exc:
        if operation == "get":
            await get_part_parameter(99)
        else:
            await update_part_parameter(99, note="changed")
    assert exc.value is error
    _assert_part_scope(mock_parameter_class)
    mock_parameter_class.return_value.save.assert_not_called()


@pytest.mark.parametrize("operation", ["list", "create"])
async def test_missing_part_rejected(
    operation: str, mock_part_class: MagicMock, mock_parameter_class: MagicMock
) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter, list_part_parameters

    mock_part_class.objects.get.side_effect = LookupError("Missing part")
    with pytest.raises(LookupError, match="Missing part"):
        if operation == "list":
            await list_part_parameters(99)
        else:
            await create_part_parameter(99, 3, "10")
    mock_parameter_class.assert_not_called()
    mock_parameter_class.objects.filter.assert_not_called()


@pytest.mark.parametrize("operation", ["get", "create"])
async def test_missing_template_rejected(
    operation: str, mock_parameter_template_class: MagicMock, mock_parameter_class: MagicMock
) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter, get_parameter_template

    mock_parameter_template_class.objects.get.side_effect = LookupError("Missing template")
    with pytest.raises(LookupError, match="Missing template"):
        if operation == "get":
            await get_parameter_template(99)
        else:
            await create_part_parameter(42, 99, "10")
    mock_parameter_class.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_save_failure_propagates_and_exits_transaction(operation: str, mock_parameter_class: MagicMock) -> None:
    from django.db import transaction

    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter, update_part_parameter

    parameter = _parameter()
    error = RuntimeError("Database constraint")
    parameter.save.side_effect = error
    mock_parameter_class.return_value = parameter
    mock_parameter_class.objects.get.return_value = parameter
    with pytest.raises(RuntimeError) as exc:
        if operation == "create":
            await create_part_parameter(42, 3, "10")
        else:
            await update_part_parameter(7, data="20")
    assert exc.value is error
    parameter.full_clean.assert_called_once_with()
    assert transaction.atomic.return_value.__exit__.call_args.args[:2] == (RuntimeError, error)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("model_type", ["generic", "part", "other"])
async def test_template_applicability(
    operation: str,
    model_type: str,
    mock_parameter_class: MagicMock,
    mock_parameter_template_class: MagicMock,
) -> None:
    from django.contrib.contenttypes.models import ContentType

    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter, update_part_parameter

    ContentType.objects.get_for_model.return_value.pk = 9
    template = _template()
    template.model_type_id = {"generic": None, "part": 9, "other": 10}[model_type]
    mock_parameter_template_class.objects.get.return_value = template
    parameter = _parameter()
    mock_parameter_class.return_value = parameter
    mock_parameter_class.objects.get.return_value = parameter

    async def write() -> dict:
        if operation == "create":
            return await create_part_parameter(42, 3, "10")
        return await update_part_parameter(7, data="20")

    if model_type == "other":
        with pytest.raises(ValueError, match="does not apply to parts"):
            await write()
        parameter.full_clean.assert_not_called()
        parameter.save.assert_not_called()
    else:
        assert (await write())["id"] == 7
        parameter.full_clean.assert_called_once_with()
        parameter.save.assert_called_once_with()
    mock_parameter_template_class.objects.select_for_update.assert_called_once_with()
    mock_parameter_template_class.objects.get.assert_called_once_with(pk=3)


@pytest.mark.parametrize("operation", ["create", "update"])
async def test_disabled_template_create_rejected_update_allowed(
    operation: str, mock_parameter_class: MagicMock, mock_parameter_template_class: MagicMock
) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter, update_part_parameter

    template = _template()
    template.enabled = False
    mock_parameter_template_class.objects.get.return_value = template
    parameter = _parameter()
    mock_parameter_class.return_value = parameter
    mock_parameter_class.objects.get.return_value = parameter
    if operation == "create":
        with pytest.raises(ValueError, match="disabled template"):
            await create_part_parameter(42, 3, "10")
        parameter.full_clean.assert_not_called()
        parameter.save.assert_not_called()
    else:
        assert (await update_part_parameter(7, note="Revised"))["note"] == "Revised"
        parameter.full_clean.assert_called_once_with()
        parameter.save.assert_called_once_with()


@pytest.mark.parametrize("operation", ["get", "create", "update"])
@pytest.mark.parametrize("data", [True, False])
async def test_checkbox_response_is_string(operation: str, data: bool, mock_parameter_class: MagicMock) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import (
        create_part_parameter,
        get_part_parameter,
        update_part_parameter,
    )

    parameter = _parameter()
    parameter.data = data
    mock_parameter_class.return_value = parameter
    mock_parameter_class.objects.get.return_value = parameter
    if operation == "get":
        result = await get_part_parameter(7)
    elif operation == "create":
        result = await create_part_parameter(42, 3, str(data))
    else:
        result = await update_part_parameter(7, note="Revised")
    assert result["data"] == str(data)


async def test_update_lock_order_and_fresh_read(
    mock_parameter_class: MagicMock, mock_parameter_template_class: MagicMock
) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import update_part_parameter

    initial = _parameter()
    locked = _parameter()
    locked.note = "Concurrent writer's note"
    mock_parameter_class.objects.get.side_effect = [initial, locked]
    mock_parameter_template_class.objects.get.return_value = _template()
    events = MagicMock()
    events.attach_mock(mock_parameter_class.objects.get, "parameter_get")
    events.attach_mock(mock_parameter_template_class.objects.select_for_update, "template_lock")
    events.attach_mock(mock_parameter_template_class.objects.get, "template_get")
    events.attach_mock(mock_parameter_class.objects.all.return_value.select_for_update, "parameter_lock")
    events.attach_mock(locked.full_clean, "validate")
    events.attach_mock(locked.save, "save")
    assert (await update_part_parameter(7, data="20"))["note"] == "Concurrent writer's note"
    assert events.mock_calls == [
        call.parameter_get(pk=7),
        call.template_lock(),
        call.template_get(pk=3),
        call.parameter_lock(),
        call.parameter_get(pk=7, template_id=3),
        call.validate(),
        call.save(),
    ]
    initial.save.assert_not_called()


@pytest.mark.parametrize("duplicate", [True, False])
async def test_create_integrity_error_translation_after_rollback(
    duplicate: bool, mock_parameter_class: MagicMock
) -> None:
    from django.contrib.contenttypes.models import ContentType
    from django.db import transaction
    from django.db.utils import IntegrityError

    from inventree_mcp_plugin.tools.simple.parameters import create_part_parameter

    parameter = _parameter()
    mock_parameter_class.return_value = parameter
    error = IntegrityError("Constraint violated")
    parameter.save.side_effect = error

    def exists() -> bool:
        transaction.atomic.return_value.__exit__.assert_called_once()
        return duplicate

    mock_parameter_class.objects.all.return_value.exists.side_effect = exists
    if duplicate:
        with pytest.raises(ValueError, match="already exists for this part and template") as exc:
            await create_part_parameter(42, 3, "10")
        assert exc.value.__cause__ is error
    else:
        with pytest.raises(IntegrityError) as exc:
            await create_part_parameter(42, 3, "10")
        assert exc.value is error
    mock_parameter_class.objects.filter.assert_called_once_with(
        model_type=ContentType.objects.get_for_model.return_value, model_id=42, template_id=3
    )


async def test_minimum_host_version_for_category_owned_parameters() -> None:
    from inventree_mcp_plugin.core import InvenTreeMCPPlugin

    assert InvenTreeMCPPlugin.MIN_VERSION == "1.4.0"
