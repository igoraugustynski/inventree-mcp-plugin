"""Selection lists and entries. Direct ORM access bypasses REST role checks."""

from __future__ import annotations

__all__: list[str] = []

from typing import Any

from ...mcp_server import mcp
from ...tools import _project, django_orm
from . import parameters


def _list_row(selection: Any, fields: list[str] | None = None) -> dict[str, Any]:
    return _project(
        {
            "id": selection.pk,
            "name": selection.name,
            "description": selection.description,
            "active": selection.active,
            "locked": selection.locked,
            "default": selection.default_id,
        },
        fields,
    )


def _entry_row(entry: Any, fields: list[str] | None = None) -> dict[str, Any]:
    return _project(
        {
            "id": entry.pk,
            "selection_list": entry.list_id,
            "value": entry.value,
            "label": entry.label,
            "description": entry.description,
            "active": entry.active,
        },
        fields,
    )


def _editable(selection: Any) -> None:
    if selection.locked:
        raise ValueError("Selection list is locked")


@mcp.tool()
@django_orm
def list_selection_lists(
    search: str | None = None,
    active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List selection lists with optional search and active filter.

    fields: id, name, description, active, locked, default. id is always included.
    limit and offset must be nonnegative; results are ordered by ID.
    """
    from common.models import SelectionList
    from django.db.models import Q

    parameters._validate_paging(limit, offset)
    queryset = SelectionList.objects.all()
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search))
    if active is not None:
        queryset = queryset.filter(active=active)
    return [_list_row(s, fields) for s in queryset.order_by("pk")[offset : offset + limit]]


@mcp.tool()
@django_orm
def get_selection_list(selection_list_id: int, fields: list[str] | None = None) -> dict[str, Any]:
    """Get a selection list including entries ordered by ID.

    fields: id, name, description, active, locked, default, entries. id is always included.
    """
    from common.models import SelectionList

    selection = SelectionList.objects.get(pk=selection_list_id)
    row = _list_row(selection)
    if fields is None or "entries" in fields:
        row["entries"] = [_entry_row(e) for e in selection.entries.order_by("pk")]
    return _project(row, fields)


@mcp.tool()
@django_orm
def create_selection_list(name: str, description: str = "", active: bool = True) -> dict[str, Any]:
    """Create a manually managed selection list, validating before save."""
    from common.models import SelectionList
    from django.db import transaction
    from django.db.utils import IntegrityError

    try:
        with transaction.atomic():
            selection = SelectionList(name=name, description=description, active=active)
            selection.full_clean()
            selection.save()
            return _list_row(selection)
    except IntegrityError as exc:
        if SelectionList.objects.filter(name=name).exists():
            raise ValueError("A selection list with this name already exists") from exc
        raise


@mcp.tool()
@django_orm
def update_selection_list(
    selection_list_id: int,
    name: str | None = None,
    description: str | None = None,
    active: bool | None = None,
    default_entry_id: int | None = None,
    clear_default: bool = False,
) -> dict[str, Any]:
    """Edit an unlocked list; omitted fields are preserved.

    default_entry_id must belong to this list. clear_default detaches the default
    and conflicts with default_entry_id. Lock/source fields cannot be edited.
    """
    from common.models import SelectionList, SelectionListEntry
    from django.db import transaction

    if clear_default and default_entry_id is not None:
        raise ValueError("default_entry_id and clear_default are mutually exclusive")
    changes: dict[str, Any] = {
        key: value
        for key, value in {"name": name, "description": description, "active": active}.items()
        if value is not None
    }
    if not changes and default_entry_id is None and not clear_default:
        raise ValueError("At least one change must be supplied")
    with transaction.atomic():
        selection = SelectionList.objects.select_for_update().get(pk=selection_list_id)
        _editable(selection)
        if default_entry_id is not None:
            entry = SelectionListEntry.objects.get(pk=default_entry_id)
            if entry.list_id != selection.pk:
                raise ValueError("Default entry must belong to this selection list")
            if not entry.active:
                raise ValueError("Default entry must be active")
            entry = SelectionListEntry.objects.select_for_update().get(
                pk=default_entry_id, list_id=selection.pk, active=True
            )
            changes["default"] = entry
        elif clear_default:
            changes["default"] = None
        parameters._save_changes(selection, changes)
        return _list_row(selection)


@mcp.tool()
@django_orm
def list_selection_list_entries(
    selection_list_id: int,
    active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List entries of an existing selection list, ordered by ID.

    fields: id, selection_list, value, label, description, active. id is always included.
    limit and offset must be nonnegative. active=None includes inactive entries.
    """
    from common.models import SelectionList, SelectionListEntry

    parameters._validate_paging(limit, offset)
    selection = SelectionList.objects.get(pk=selection_list_id)
    queryset = SelectionListEntry.objects.filter(list=selection)
    if active is not None:
        queryset = queryset.filter(active=active)
    return [_entry_row(e, fields) for e in queryset.order_by("pk")[offset : offset + limit]]


@mcp.tool()
@django_orm
def get_selection_list_entry(entry_id: int, fields: list[str] | None = None) -> dict[str, Any]:
    """Get an entry. fields: id, selection_list, value, label, description, active; id is always included."""
    from common.models import SelectionListEntry

    return _entry_row(SelectionListEntry.objects.get(pk=entry_id), fields)


@mcp.tool()
@django_orm
def create_selection_list_entry(
    selection_list_id: int,
    value: str,
    label: str,
    description: str = "",
    active: bool = True,
) -> dict[str, Any]:
    """Create a validated entry in an unlocked list. Duplicate list/value pairs are rejected.

    Parent-list locks coordinate these tools only, not upstream writers.
    """
    from common.models import SelectionList, SelectionListEntry
    from django.db import transaction
    from django.db.utils import IntegrityError

    try:
        with transaction.atomic():
            selection = SelectionList.objects.select_for_update().get(pk=selection_list_id)
            _editable(selection)
            entry = SelectionListEntry(list=selection, value=value, label=label, description=description, active=active)
            entry.full_clean()
            entry.save()
            return _entry_row(entry)
    except IntegrityError as exc:
        if SelectionListEntry.objects.filter(list_id=selection_list_id, value=value).exists():
            raise ValueError("An entry with this value already exists in the selection list") from exc
        raise


@mcp.tool()
@django_orm
def update_selection_list_entry(
    entry_id: int,
    value: str | None = None,
    label: str | None = None,
    description: str | None = None,
    active: bool | None = None,
) -> dict[str, Any]:
    """Edit an entry of an unlocked list, preserving omitted fields; entries cannot be reparented.

    Lock order is parent list then entry; locks coordinate these tools only.
    Value/active edits can invalidate existing parameters. They do not rewrite
    stored parameter values or validate every dependent resource.
    """
    from common.models import SelectionList, SelectionListEntry
    from django.db import transaction

    changes = parameters._changes(value=value, label=label, description=description, active=active)
    with transaction.atomic():
        initial = SelectionListEntry.objects.get(pk=entry_id)
        selection = SelectionList.objects.select_for_update().get(pk=initial.list_id)
        _editable(selection)
        entry = SelectionListEntry.objects.select_for_update().get(pk=entry_id, list_id=selection.pk)
        if active is False and selection.default_id == entry.pk:
            raise ValueError("Change or clear the list default before deactivating this entry")
        parameters._save_changes(entry, changes)
        return _entry_row(entry)
