"""Template, selection, category default and category value contracts."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from .test_parameters import _parameter, _template


def _selection() -> MagicMock:
    item = MagicMock()
    item.pk = 4
    item.name = "Ratings"
    item.description = "Available ratings"
    item.active = True
    item.locked = False
    item.default_id = None
    return item


def _entry() -> MagicMock:
    item = MagicMock()
    item.pk = 8
    item.list_id = 4
    item.value = "10"
    item.label = "Ten"
    item.description = "Ten ohms"
    item.active = True
    return item


def _assignment() -> MagicMock:
    item = MagicMock()
    item.pk = 6
    item.category_id = 42
    item.template_id = 3
    item.default_value = "10"
    return item


@pytest.fixture()
def resources(
    mock_parameter_class: MagicMock,
    mock_parameter_template_class: MagicMock,
    mock_selection_list_class: MagicMock,
    mock_selection_list_entry_class: MagicMock,
    mock_category_parameter_template_class: MagicMock,
    mock_part_category_class: MagicMock,
) -> dict[str, MagicMock]:
    models = {
        "parameter": mock_parameter_class,
        "template": mock_parameter_template_class,
        "selection": mock_selection_list_class,
        "entry": mock_selection_list_entry_class,
        "assignment": mock_category_parameter_template_class,
    }
    for key, factory in {
        "parameter": _parameter,
        "template": _template,
        "selection": _selection,
        "entry": _entry,
        "assignment": _assignment,
    }.items():
        item = factory()
        models[key].return_value = item
        models[key].objects.get.return_value = item
        models[key].objects.all.return_value.__getitem__.return_value = [item]
    mock_part_category_class.objects.get.return_value.pk = 42
    return models


def _tool(name: str):
    from inventree_mcp_plugin.tools.simple import category_parameters, parameters, selection_lists

    for module in (parameters, selection_lists, category_parameters):
        if hasattr(module, name):
            return getattr(module, name)
    raise AssertionError(name)


@pytest.mark.parametrize("mode", [None, 0, 1, 2])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_category_defaults_reject_uniqueness_templates(
    mode: int | None, operation: str, resources: dict[str, MagicMock]
) -> None:
    template = resources["template"].objects.get.return_value
    assignment = resources["assignment"].return_value
    if mode is None:
        del template.unique  # Hosts without template-level value uniqueness.
    else:
        template.unique = mode
    tool = _tool(f"{operation}_category_parameter_template")
    kwargs = {"category_id": 42, "template_id": 3} if operation == "create" else {"assignment_id": 6}
    if mode in (1, 2):
        with pytest.raises(ValueError, match="Uniqueness-enforcing"):
            await tool(**kwargs, default_value="10")
        assignment.full_clean.assert_not_called()
        assignment.save.assert_not_called()
    else:
        assert (await tool(**kwargs, default_value="10"))["id"] == 6
        assignment.save.assert_called_once_with()


_WRITES = [
    ("create_parameter_template", {"name": "Resistance"}, "template"),
    ("update_parameter_template", {"template_id": 3, "description": "New"}, "template"),
    ("create_selection_list", {"name": "Ratings"}, "selection"),
    ("update_selection_list", {"selection_list_id": 4, "description": "New"}, "selection"),
    ("create_selection_list_entry", {"selection_list_id": 4, "value": "10", "label": "Ten"}, "entry"),
    ("update_selection_list_entry", {"entry_id": 8, "label": "New"}, "entry"),
    ("create_category_parameter_template", {"category_id": 42, "template_id": 3}, "assignment"),
    ("update_category_parameter_template", {"assignment_id": 6, "default_value": "10"}, "assignment"),
    ("create_category_parameter", {"category_id": 42, "template_id": 3, "data": "10"}, "parameter"),
    ("update_category_parameter", {"parameter_id": 7, "note": "New"}, "parameter"),
]


@pytest.mark.parametrize("name,kwargs,model", _WRITES)
async def test_writes_validate_then_save_atomically(name: str, kwargs: dict, model: str, resources: dict) -> None:
    from django.db import transaction

    item = resources[model].return_value
    result = await _tool(name)(**kwargs)
    assert result["id"] == item.pk
    item.full_clean.assert_called_once_with()
    item.save.assert_called_once_with()
    assert item.mock_calls.index(call.full_clean()) < item.mock_calls.index(call.save())
    transaction.atomic.return_value.__enter__.assert_called_once()
    transaction.atomic.return_value.__exit__.assert_called_once_with(None, None, None)


@pytest.mark.parametrize("name,kwargs,model", _WRITES)
async def test_write_validation_propagates_no_save(name: str, kwargs: dict, model: str, resources: dict) -> None:
    from django.db import transaction

    item = resources[model].return_value
    error = ValueError("Host validation")
    item.full_clean.side_effect = error
    with pytest.raises(ValueError) as exc:
        await _tool(name)(**kwargs)
    assert exc.value is error
    item.save.assert_not_called()
    assert transaction.atomic.return_value.__exit__.call_args.args[:2] == (ValueError, error)


@pytest.mark.parametrize("name,kwargs,model", _WRITES)
async def test_missing_write_dependencies(name: str, kwargs: dict, model: str, resources: dict) -> None:
    dependency = {
        "create_parameter_template": None,
        "update_parameter_template": "template",
        "create_selection_list": None,
        "update_selection_list": "selection",
        "create_selection_list_entry": "selection",
        "update_selection_list_entry": "entry",
        "create_category_parameter_template": "template",
        "update_category_parameter_template": "assignment",
        "create_category_parameter": "template",
        "update_category_parameter": "parameter",
    }[name]
    if dependency is None:
        return
    resources[dependency].objects.get.side_effect = LookupError("Missing ID")
    with pytest.raises(LookupError, match="Missing ID"):
        await _tool(name)(**kwargs)
    resources[model].return_value.save.assert_not_called()


@pytest.mark.parametrize(
    "name,kwargs,model,projection",
    [
        ("list_selection_lists", {"search": "Rat", "active": False}, "selection", "name"),
        ("list_selection_list_entries", {"selection_list_id": 4, "active": False}, "entry", "label"),
        ("list_category_parameter_templates", {"category_id": 42, "template_id": 3}, "assignment", "default_value"),
        ("list_category_parameters", {"category_id": 42, "template_id": 3}, "parameter", "note"),
    ],
)
async def test_lists_filter_page_project(name: str, kwargs: dict, model: str, projection: str, resources: dict) -> None:
    result = await _tool(name)(**kwargs, limit=2, offset=3, fields=[projection, "unknown"])
    item = resources[model].return_value
    assert result == [{"id": item.pk, projection: getattr(item, projection)}]
    qs = resources[model].objects.all.return_value
    qs.order_by.assert_called_once_with("pk")
    qs.__getitem__.assert_called_once_with(slice(3, 5))
    if "active" in kwargs:
        qs.filter.assert_any_call(active=False)
    if name == "list_category_parameter_templates":
        assert qs.filter.call_args_list == [call(category_id=42), call(template_id=3)]
    elif name == "list_selection_list_entries":
        resources[model].objects.filter.assert_called_once_with(list=resources["selection"].return_value)
    elif name == "list_category_parameters":
        from django.contrib.contenttypes.models import ContentType
        from part.models import PartCategory

        ContentType.objects.get_for_model.assert_called_once_with(PartCategory)
        resources[model].objects.filter.assert_called_once_with(
            model_type=ContentType.objects.get_for_model.return_value, model_id=42
        )
        qs.filter.assert_called_once_with(template_id=3)


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("list_selection_lists", {}),
        ("list_selection_list_entries", {"selection_list_id": 4}),
        ("list_category_parameter_templates", {}),
        ("list_category_parameters", {"category_id": 42}),
        ("list_parameter_templates", {}),
    ],
)
@pytest.mark.parametrize("paging", [{"limit": -1}, {"offset": -1}])
async def test_lists_reject_negative_paging(name: str, kwargs: dict, paging: dict, resources: dict) -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        await _tool(name)(**kwargs, **paging)


@pytest.mark.parametrize(
    "name,kwargs,model",
    [
        ("get_selection_list", {"selection_list_id": 4}, "selection"),
        ("get_selection_list_entry", {"entry_id": 8}, "entry"),
        ("get_category_parameter_template", {"assignment_id": 6}, "assignment"),
        ("get_category_parameter", {"parameter_id": 7}, "parameter"),
    ],
)
async def test_get_projection_and_missing(name: str, kwargs: dict, model: str, resources: dict) -> None:
    assert await _tool(name)(**kwargs, fields=["unknown"]) == {"id": resources[model].return_value.pk}
    resources[model].objects.get.side_effect = LookupError("Missing ID")
    with pytest.raises(LookupError, match="Missing ID"):
        await _tool(name)(**kwargs)


async def test_list_detail_has_ordered_entries(resources: dict) -> None:
    selection = resources["selection"].return_value
    selection.entries.order_by.return_value = [_entry()]
    result = await _tool("get_selection_list")(4)
    assert result["entries"] == [
        {
            "id": 8,
            "selection_list": 4,
            "value": "10",
            "label": "Ten",
            "description": "Ten ohms",
            "active": True,
        }
    ]
    selection.entries.order_by.assert_called_once_with("pk")


@pytest.mark.parametrize("model_type", [None, "part.part", "part.partcategory"])
async def test_template_creation_model_and_selection_fk(model_type: str | None, resources: dict) -> None:
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part, PartCategory

    await _tool("create_parameter_template")("New", model_type=model_type, selection_list_id=4)
    kwargs = resources["template"].call_args.kwargs
    assert kwargs["selectionlist"] is resources["selection"].return_value
    if model_type is None:
        assert kwargs["model_type"] is None
        ContentType.objects.get_for_model.assert_not_called()
    else:
        ContentType.objects.get_for_model.assert_called_once_with(Part if model_type == "part.part" else PartCategory)
        assert kwargs["model_type"] is ContentType.objects.get_for_model.return_value


async def test_template_read_filter_all_status_category_and_metadata(resources: dict) -> None:
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q
    from part.models import PartCategory

    result = await _tool("list_parameter_templates")(model_type="part.partcategory", enabled=None)
    ContentType.objects.get_for_model.assert_called_once_with(PartCategory)
    assert Q.call_args_list == [
        call(model_type__isnull=True),
        call(model_type=ContentType.objects.get_for_model.return_value),
    ]
    resources["template"].objects.all.return_value.filter.assert_not_called()
    assert result[0]["choices_raw"] == "10,20"
    assert result[0]["selection_list"] is None


@pytest.mark.parametrize("kwargs", [{}, {"selection_list_id": 4, "clear_selection_list": True}])
async def test_template_update_rejects_noop_conflict(kwargs: dict, resources: dict) -> None:
    with pytest.raises(ValueError):
        await _tool("update_parameter_template")(3, **kwargs)
    resources["template"].return_value.save.assert_not_called()


async def test_template_update_preserves_and_clears(resources: dict) -> None:
    template = resources["template"].return_value
    await _tool("update_parameter_template")(3, enabled=False, clear_selection_list=True)
    assert template.enabled is False
    assert template.selectionlist is None
    assert template.name == "Resistance"
    assert template.model_type_id is None
    resources["template"].objects.select_for_update.assert_called_once_with()


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("invalid", ["raw_choices", "checkbox", "inactive"])
async def test_template_selection_invariants(operation: str, invalid: str, resources: dict) -> None:
    template = resources["template"].return_value
    template.choices = ""
    selection = resources["selection"].return_value
    kwargs = {"selection_list_id": 4}
    if invalid == "raw_choices":
        kwargs["choices"] = "10,20"
    elif invalid == "checkbox":
        kwargs["checkbox"] = True
    else:
        selection.active = False
    with pytest.raises(ValueError):
        if operation == "create":
            await _tool("create_parameter_template")("New", **kwargs)
        else:
            await _tool("update_parameter_template")(3, **kwargs)
    template.full_clean.assert_not_called()
    template.save.assert_not_called()


async def test_existing_inactive_selection_metadata_edit_allowed(resources: dict) -> None:
    template = resources["template"].return_value
    template.choices = ""
    template.selectionlist_id = 4
    template.selectionlist = resources["selection"].return_value
    template.selectionlist.active = False
    await _tool("update_parameter_template")(3, description="New")
    template.save.assert_called_once_with()


@pytest.mark.parametrize("kwargs", [{"choices": "20,30"}, {"checkbox": True}])
async def test_template_prospective_effective_options(kwargs: dict, resources: dict) -> None:
    template = resources["template"].return_value
    template.choices = ""
    template.selectionlist_id = 4
    template.selectionlist = resources["selection"].return_value
    with pytest.raises(ValueError):
        await _tool("update_parameter_template")(3, **kwargs)
    template.save.assert_not_called()
    await _tool("update_parameter_template")(3, clear_selection_list=True, **kwargs)
    template.save.assert_called_once_with()


async def test_template_attach_requires_clearing_existing_raw_choices(resources: dict) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        await _tool("update_parameter_template")(3, selection_list_id=4)
    await _tool("update_parameter_template")(3, selection_list_id=4, choices="")
    assert resources["template"].return_value.selectionlist is resources["selection"].return_value


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("update_selection_list", {"selection_list_id": 4, "name": "New"}),
        ("create_selection_list_entry", {"selection_list_id": 4, "value": "20", "label": "Twenty"}),
        ("update_selection_list_entry", {"entry_id": 8, "value": "20"}),
    ],
)
async def test_locked_list_blocks_all_mutations(name: str, kwargs: dict, resources: dict) -> None:
    resources["selection"].return_value.locked = True
    with pytest.raises(ValueError, match="locked"):
        await _tool(name)(**kwargs)
    resources["selection"].return_value.save.assert_not_called()
    resources["entry"].return_value.save.assert_not_called()


@pytest.mark.parametrize("kwargs", [{}, {"default_entry_id": 8, "clear_default": True}])
async def test_selection_update_noop_conflict(kwargs: dict, resources: dict) -> None:
    with pytest.raises(ValueError):
        await _tool("update_selection_list")(4, **kwargs)


@pytest.mark.parametrize("invalid", ["other_list", "inactive"])
async def test_default_entry_membership_and_active(invalid: str, resources: dict) -> None:
    entry = resources["entry"].return_value
    if invalid == "other_list":
        entry.list_id = 5
    else:
        entry.active = False
    with pytest.raises(ValueError):
        await _tool("update_selection_list")(4, default_entry_id=8)
    resources["entry"].objects.select_for_update.assert_not_called()
    resources["selection"].return_value.save.assert_not_called()


async def test_default_set_clear_and_entry_edit_preservation(resources: dict) -> None:
    selection = resources["selection"].return_value
    await _tool("update_selection_list")(4, default_entry_id=8)
    assert selection.default is resources["entry"].return_value
    resources["entry"].objects.get.assert_any_call(pk=8, list_id=4, active=True)
    await _tool("update_selection_list")(4, clear_default=True)
    assert selection.default is None
    entry = resources["entry"].return_value
    await _tool("update_selection_list_entry")(8, value="20", active=False)
    assert entry.value == "20"
    assert entry.active is False
    assert entry.label == "Ten"
    assert entry.list_id == 4
    resources["entry"].objects.get.assert_any_call(pk=8, list_id=4)


async def test_default_entry_cannot_be_deactivated(resources: dict) -> None:
    resources["selection"].return_value.default_id = 8
    with pytest.raises(ValueError, match="default before deactivating"):
        await _tool("update_selection_list_entry")(8, active=False)
    resources["entry"].return_value.save.assert_not_called()


async def test_entry_noop_and_parent_lock_order(resources: dict) -> None:
    with pytest.raises(ValueError, match="change"):
        await _tool("update_selection_list_entry")(8)
    events = MagicMock()
    events.attach_mock(resources["selection"].objects.select_for_update, "parent_lock")
    events.attach_mock(resources["entry"].objects.select_for_update, "entry_lock")
    await _tool("update_selection_list_entry")(8, label="Updated")
    assert events.mock_calls == [call.parent_lock(), call.entry_lock()]


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("model_id", [None, 11, 12])
async def test_category_and_assignment_applicability(operation: str, model_id: int | None, resources: dict) -> None:
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part, PartCategory

    template = resources["template"].return_value
    template.model_type_id = model_id
    ContentType.objects.get_for_model.side_effect = lambda model: MagicMock(pk=11 if model is Part else 12)
    for kind in ("parameter_template", "parameter"):
        ContentType.objects.get_for_model.reset_mock()
        if kind == "parameter_template":
            kwargs = {"category_id": 42, "template_id": 3} if operation == "create" else {"assignment_id": 6}
            kwargs["default_value"] = "10"
            applicable = model_id in (None, 11)
        else:
            kwargs = {"category_id": 42, "template_id": 3} if operation == "create" else {"parameter_id": 7}
            kwargs["data"] = "10"
            applicable = model_id in (None, 12)
        if applicable:
            await _tool(f"{operation}_category_{kind}")(**kwargs)
        else:
            with pytest.raises(ValueError, match="does not apply"):
                await _tool(f"{operation}_category_{kind}")(**kwargs)
        ContentType.objects.get_for_model.assert_called_once_with(
            Part if kind == "parameter_template" else PartCategory
        )


@pytest.mark.parametrize("kind", ["parameter_template", "parameter"])
async def test_category_disabled_create_rejected_existing_update_allowed(kind: str, resources: dict) -> None:
    resources["template"].return_value.enabled = False
    kwargs = {"category_id": 42, "template_id": 3}
    kwargs["default_value" if kind == "parameter_template" else "data"] = "10"
    with pytest.raises(ValueError, match="disabled"):
        await _tool(f"create_category_{kind}")(**kwargs)
    update = (
        {"assignment_id": 6, "default_value": "10"}
        if kind == "parameter_template"
        else {"parameter_id": 7, "note": "New"}
    )
    await _tool(f"update_category_{kind}")(**update)


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("value,valid", [("", True), (" 10 ", True), ("30", False)])
async def test_assignment_default_choices(operation: str, value: str, valid: bool, resources: dict) -> None:
    assignment = resources["assignment"].return_value
    assignment.default_value = value
    assignment.full_clean.side_effect = lambda: setattr(assignment, "default_value", assignment.default_value.strip())
    kwargs = {"category_id": 42, "template_id": 3} if operation == "create" else {"assignment_id": 6}
    if valid:
        result = await _tool(f"{operation}_category_parameter_template")(**kwargs, default_value=value)
        assert result["default_value"] == value.strip()
    else:
        with pytest.raises(ValueError, match="valid template choice"):
            await _tool(f"{operation}_category_parameter_template")(**kwargs, default_value=value)
        assignment.save.assert_not_called()


async def test_category_parameter_scope_links_and_preservation(resources: dict) -> None:
    from django.contrib.contenttypes.models import ContentType
    from part.models import PartCategory

    result = await _tool("create_category_parameter")(42, 3, "10", note="New")
    assert result["category"] == 42
    assert "part" not in result
    kwargs = resources["parameter"].call_args.kwargs
    assert kwargs["model_id"] == 42
    assert kwargs["template"] is resources["template"].return_value
    ContentType.objects.get_for_model.assert_called_once_with(PartCategory)
    await _tool("update_category_parameter")(7, data="20")
    assert resources["parameter"].return_value.note == "Original note"
    resources["parameter"].objects.filter.assert_called_with(model_type=ContentType.objects.get_for_model.return_value)
    with pytest.raises(ValueError, match="At least one"):
        await _tool("update_category_parameter")(7)


async def test_assignment_fk_and_immutable_links(resources: dict, mock_part_category_class: MagicMock) -> None:
    await _tool("create_category_parameter_template")(42, 3, default_value="10")
    kwargs = resources["assignment"].call_args.kwargs
    assert kwargs == {
        "category": mock_part_category_class.objects.get.return_value,
        "template": resources["template"].return_value,
        "default_value": "10",
    }
    await _tool("update_category_parameter_template")(6, default_value="20")
    assert resources["assignment"].return_value.category_id == 42
    assert resources["assignment"].return_value.template_id == 3


@pytest.mark.parametrize(
    "name,kwargs,parent",
    [
        ("list_selection_list_entries", {"selection_list_id": 4}, "selection"),
        ("create_selection_list_entry", {"selection_list_id": 4, "value": "10", "label": "Ten"}, "selection"),
        ("list_category_parameters", {"category_id": 42}, "category"),
        ("create_category_parameter", {"category_id": 42, "template_id": 3, "data": "10"}, "category"),
        ("create_category_parameter_template", {"category_id": 42, "template_id": 3}, "category"),
    ],
)
async def test_missing_parent_rejected(
    name: str, kwargs: dict, parent: str, resources: dict, mock_part_category_class: MagicMock
) -> None:
    model = resources["selection"] if parent == "selection" else mock_part_category_class
    model.objects.get.side_effect = LookupError("Missing parent")
    with pytest.raises(LookupError, match="Missing parent"):
        await _tool(name)(**kwargs)
    for key in ("entry", "parameter", "assignment"):
        resources[key].return_value.save.assert_not_called()


@pytest.mark.parametrize("name,kwargs,model", [case for case in _WRITES if case[0].startswith("create_")])
@pytest.mark.parametrize("duplicate", [True, False])
async def test_duplicate_integrity_translation_after_rollback(
    name: str, kwargs: dict, model: str, duplicate: bool, resources: dict
) -> None:
    from django.db import transaction
    from django.db.utils import IntegrityError

    error = IntegrityError("Constraint")
    resources[model].return_value.save.side_effect = error

    def exists() -> bool:
        transaction.atomic.return_value.__exit__.assert_called_once()
        return duplicate

    resources[model].objects.all.return_value.exists.side_effect = exists
    with pytest.raises(ValueError if duplicate else IntegrityError) as exc:
        await _tool(name)(**kwargs)
    assert (exc.value.__cause__ if duplicate else exc.value) is error


def _linked_choices(resources: dict, inactive_only: bool) -> MagicMock:
    template = resources["template"].return_value
    template.selectionlist_id = 4
    selection = resources["selection"].return_value
    entries = []
    if inactive_only:
        entry = _entry()
        entry.active = False
        entries.append(entry)
    selection.get_choices.side_effect = lambda: [entry.value for entry in entries if entry.active]
    template.get_choices.side_effect = selection.get_choices
    return template


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("create_part_parameter", {"part_id": 42, "template_id": 3, "data": "10"}),
        ("update_part_parameter", {"parameter_id": 7, "data": "10"}),
        ("update_part_parameter", {"parameter_id": 7, "note": "Note only"}),
        ("create_category_parameter", {"category_id": 42, "template_id": 3, "data": "10"}),
        ("update_category_parameter", {"parameter_id": 7, "data": "10"}),
        ("update_category_parameter", {"parameter_id": 7, "note": "Note only"}),
    ],
)
@pytest.mark.parametrize("inactive_only", [False, True])
async def test_empty_linked_choices_reject_parameter_writes(
    name: str, kwargs: dict, inactive_only: bool, resources: dict
) -> None:
    template = _linked_choices(resources, inactive_only)
    with pytest.raises(ValueError, match="valid selection list choice"):
        await _tool(name)(**kwargs)
    template.get_choices.assert_called_once_with()
    resources["parameter"].return_value.full_clean.assert_not_called()
    resources["parameter"].return_value.save.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("inactive_only", [False, True])
@pytest.mark.parametrize("default", ["", " 10 "])
async def test_empty_linked_choices_assignment_defaults(
    operation: str, inactive_only: bool, default: str, resources: dict
) -> None:
    _linked_choices(resources, inactive_only)
    assignment = resources["assignment"].return_value
    assignment.default_value = default
    kwargs = {"category_id": 42, "template_id": 3} if operation == "create" else {"assignment_id": 6}
    if default:
        with pytest.raises(ValueError, match="valid selection list choice"):
            await _tool(f"{operation}_category_parameter_template")(**kwargs, default_value=default)
        assignment.full_clean.assert_not_called()
        assignment.save.assert_not_called()
    else:
        await _tool(f"{operation}_category_parameter_template")(**kwargs, default_value=default)
        assignment.full_clean.assert_called_once_with()
        assignment.save.assert_called_once_with()


async def test_linked_choice_guard_preserves_inline_host_validation(resources: dict) -> None:
    from inventree_mcp_plugin.tools.simple.parameters import _validate_parameter_choices

    template = resources["template"].return_value
    _validate_parameter_choices(template, "arbitrary")
    template.get_choices.assert_not_called()
    template.selectionlist_id = 4
    _validate_parameter_choices(template, "10")
    with pytest.raises(ValueError, match="valid selection list choice"):
        _validate_parameter_choices(template, "30")
