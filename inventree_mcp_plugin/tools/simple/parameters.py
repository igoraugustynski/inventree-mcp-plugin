"""Templates and part parameters using InvenTree's generic parameter models.

Like other ORM tools, these bypass REST API per-object permission checks.
"""

from __future__ import annotations

__all__: list[str] = []

from typing import Any, Literal

from ...mcp_server import mcp
from ...tools import _project, django_orm


def _validate_paging(limit: int, offset: int) -> None:
    if limit < 0 or offset < 0:
        raise ValueError("limit and offset must be nonnegative")


def _validate_template(template: Any, part_type: Any, *, creating: bool, resource: str = "parts") -> None:
    if template.model_type_id is not None and template.model_type_id != part_type.pk:
        raise ValueError(f"Parameter template does not apply to {resource}")
    if creating and not template.enabled:
        raise ValueError("Cannot create a parameter from a disabled template")


def _validate_parameter_choices(template: Any, data: str) -> None:
    """A linked list with no active entries permits no values, not arbitrary text."""
    if template.selectionlist_id is not None and data not in template.get_choices():
        raise ValueError("Parameter value is not a valid selection list choice")


def _template_row(template: Any, fields: list[str] | None) -> dict[str, Any]:
    return _project(
        {
            "id": template.pk,
            "name": template.name,
            "description": template.description,
            "units": template.units,
            "choices": template.get_choices(),
            "checkbox": template.checkbox,
            "enabled": template.enabled,
            "model_type": template.model_type_id,
            "choices_raw": template.choices,
            "selection_list": template.selectionlist_id,
        },
        fields,
    )


def _parameter_row(parameter: Any, fields: list[str] | None = None, resource: str = "part") -> dict[str, Any]:
    return _project(
        {
            "id": parameter.pk,
            resource: parameter.model_id,
            "template": parameter.template_id,
            "name": parameter.template.name,
            "units": parameter.template.units,
            "data": str(parameter.data),
            "data_numeric": parameter.data_numeric,
            "note": parameter.note,
        },
        fields,
    )


@mcp.tool()
@django_orm
def list_parameter_templates(
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    fields: list[str] | None = None,
    model_type: Literal["part.part", "part.partcategory"] = "part.part",
    enabled: bool | None = True,
) -> list[dict[str, Any]]:
    """List applicable templates, including generic templates; enabled only by default.

    Args:
        search: Case-insensitive name or description search.
        limit: Maximum results, nonnegative.
        offset: Results to skip, nonnegative.
        fields: Fields to include: id, name, description, units, choices, checkbox,
                enabled, model_type (content type ID, or null for generic), choices_raw,
                selection_list. Resolved choices reflect the linked selection list.
                Defaults to all; id is always included.
        model_type: Target model: part.part or part.partcategory. Always includes generic templates.
        enabled: Filter enabled status; None includes disabled templates too.
    """
    from common.models import ParameterTemplate
    from django.db.models import Q

    _validate_paging(limit, offset)
    part_type = _model_type(model_type)
    queryset = ParameterTemplate.objects.filter(Q(model_type__isnull=True) | Q(model_type=part_type)).select_related(
        "selectionlist"
    )
    if enabled is not None:
        queryset = queryset.filter(enabled=enabled)
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search))
    return [_template_row(t, fields) for t in queryset.order_by("pk")[offset : offset + limit]]


@mcp.tool()
@django_orm
def get_parameter_template(template_id: int, fields: list[str] | None = None) -> dict[str, Any]:
    """Get a parameter template by ID.

    Args:
        template_id: Template ID.
        fields: Fields to include: id, name, description, units, choices, checkbox,
                enabled, model_type (content type ID, or null for generic), choices_raw, selection_list.
                Defaults to all; id is always included. This tool can inspect any template.
    """
    from common.models import ParameterTemplate

    template = ParameterTemplate.objects.select_related("selectionlist").get(pk=template_id)
    return _template_row(template, fields)


@mcp.tool()
@django_orm
def list_part_parameters(
    part_id: int,
    template_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List parameters for an existing part, optionally filtered by template.

    Args:
        part_id: Part ID.
        template_id: Optional template ID filter.
        limit: Maximum results, nonnegative.
        offset: Results to skip, nonnegative.
        fields: Fields to include: id, part, template, name, units, data, data_numeric,
                note. Defaults to all; id is always included.
    """
    from common.models import Parameter
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part

    _validate_paging(limit, offset)
    part = Part.objects.get(pk=part_id)
    queryset = Parameter.objects.filter(model_type=ContentType.objects.get_for_model(Part), model_id=part.pk)
    if template_id is not None:
        queryset = queryset.filter(template_id=template_id)
    queryset = queryset.select_related("template").order_by("pk")
    return [_parameter_row(p, fields) for p in queryset[offset : offset + limit]]


@mcp.tool()
@django_orm
def get_part_parameter(parameter_id: int, fields: list[str] | None = None) -> dict[str, Any]:
    """Get a parameter by ID, restricted to parameters linked to parts.

    Args:
        parameter_id: Part parameter ID.
        fields: Fields to include: id, part, template, name, units, data, data_numeric,
                note. Defaults to all; id is always included.
    """
    from common.models import Parameter
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part

    parameter = (
        Parameter.objects
        .filter(model_type=ContentType.objects.get_for_model(Part))
        .select_related("template")
        .get(pk=parameter_id)
    )
    return _parameter_row(parameter, fields)


@mcp.tool()
@django_orm
def create_part_parameter(part_id: int, template_id: int, data: str, note: str = "") -> dict[str, Any]:
    """Create a validated part parameter. Duplicate part/template pairs are rejected.

    Requires an enabled template applicable to parts. Template locks serialize
    validation among these tools, not upstream writers. Direct ORM access
    bypasses REST API per-object permission checks.

    Args:
        part_id: Existing part ID.
        template_id: Existing template ID.
        data: Parameter value as a string, validated by InvenTree.
        note: Optional note.
    """
    from part.models import Part

    return _create_parameter(Part, "part", part_id, template_id, data, note)


def _create_parameter(
    model: Any, resource: str, model_id: int, template_id: int, data: str, note: str
) -> dict[str, Any]:
    from common.models import Parameter, ParameterTemplate
    from django.contrib.contenttypes.models import ContentType
    from django.db import transaction
    from django.db.utils import IntegrityError

    part_type = ContentType.objects.get_for_model(model)
    try:
        with transaction.atomic():
            template = ParameterTemplate.objects.select_for_update().get(pk=template_id)
            _validate_template(
                template, part_type, creating=True, resource="categories" if resource == "category" else "parts"
            )
            part = model.objects.get(pk=model_id)
            parameter = Parameter(model_type=part_type, model_id=part.pk, template=template, data=data, note=note)
            _validate_parameter_choices(template, data)
            parameter.full_clean()
            parameter.save()
            return _parameter_row(parameter, resource=resource)
    except IntegrityError as exc:
        # Query only after the failed transaction has been rolled back.
        if Parameter.objects.filter(model_type=part_type, model_id=model_id, template_id=template_id).exists():
            raise ValueError(f"A parameter already exists for this {resource} and template") from exc
        raise


@mcp.tool()
@django_orm
def update_part_parameter(parameter_id: int, data: str | None = None, note: str | None = None) -> dict[str, Any]:
    """Update a validated part parameter, preserving omitted fields.

    Args:
        parameter_id: Part parameter ID; other resource parameters cannot be updated.
        data: New value, or None to leave unchanged.
        note: New note, or None to leave unchanged. An empty string clears the note.

    At least one of data or note must be supplied. Existing values of disabled
    templates may be updated, but the template must still apply to parts.
    Template locks serialize validation among these tools, not upstream writers.
    Direct ORM access bypasses
    REST API per-object permission checks, as with other tools.
    """
    from part.models import Part

    return _update_parameter(Part, "part", parameter_id, data, note)


def _update_parameter(
    model: Any, resource: str, parameter_id: int, data: str | None, note: str | None
) -> dict[str, Any]:
    from common.models import Parameter, ParameterTemplate
    from django.contrib.contenttypes.models import ContentType
    from django.db import transaction

    if data is None and note is None:
        raise ValueError("At least one of data or note must be supplied")
    with transaction.atomic():
        part_type = ContentType.objects.get_for_model(model)
        queryset = Parameter.objects.filter(model_type=part_type)
        initial = queryset.get(pk=parameter_id)
        # All plugin writers lock template before parameter to avoid deadlocks.
        template = ParameterTemplate.objects.select_for_update().get(pk=initial.template_id)
        _validate_template(
            template, part_type, creating=False, resource="categories" if resource == "category" else "parts"
        )
        parameter = queryset.select_for_update().get(pk=parameter_id, template_id=template.pk)
        parameter.template = template
        if data is not None:
            parameter.data = data
        if note is not None:
            parameter.note = note
        _validate_parameter_choices(template, str(parameter.data))
        parameter.full_clean()
        parameter.save()
        return _parameter_row(parameter, resource=resource)


def _model_type(name: Literal["part.part", "part.partcategory"]) -> Any:
    from django.contrib.contenttypes.models import ContentType
    from part.models import Part, PartCategory

    if name not in {"part.part", "part.partcategory"}:
        raise ValueError("model_type must be part.part or part.partcategory")
    return ContentType.objects.get_for_model(Part if name == "part.part" else PartCategory)


def _changes(**values: Any) -> dict[str, Any]:
    changes = {key: value for key, value in values.items() if value is not None}
    if not changes:
        raise ValueError("At least one change must be supplied")
    return changes


def _save_changes(instance: Any, changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        setattr(instance, key, value)
    instance.full_clean()
    instance.save()


def _validate_template_options(checkbox: bool, choices: str, selection: Any, *, attaching: bool) -> None:
    if selection is not None:
        if choices.strip():
            raise ValueError("Raw choices and a selection list are mutually exclusive")
        if checkbox:
            raise ValueError("Checkbox templates cannot use a selection list")
        if attaching and not selection.active:
            raise ValueError("Cannot attach an inactive selection list")


@mcp.tool()
@django_orm
def create_parameter_template(
    name: str,
    description: str = "",
    units: str = "",
    checkbox: bool = False,
    choices: str = "",
    selection_list_id: int | None = None,
    enabled: bool = True,
    model_type: Literal["part.part", "part.partcategory"] | None = None,
) -> dict[str, Any]:
    """Create a generic, part, or category parameter template.

    choices is the raw comma-separated string, mutually exclusive with a linked
    selection list. Linked lists must be active and cannot be used for checkboxes.
    Host validation applies and host save hooks are preserved.
    model_type=None creates a generic template. REST role checks are bypassed.
    """
    from common.models import ParameterTemplate, SelectionList
    from django.db import transaction
    from django.db.utils import IntegrityError

    try:
        with transaction.atomic():
            selection = None if selection_list_id is None else SelectionList.objects.get(pk=selection_list_id)
            _validate_template_options(checkbox, choices, selection, attaching=True)
            template = ParameterTemplate(
                name=name,
                description=description,
                units=units,
                checkbox=checkbox,
                choices=choices,
                selectionlist=selection,
                enabled=enabled,
                model_type=None if model_type is None else _model_type(model_type),
            )
            template.full_clean()
            template.save()
            return _template_row(template, None)
    except IntegrityError as exc:
        if ParameterTemplate.objects.filter(name__iexact=name).exists():
            raise ValueError("A parameter template with this name already exists") from exc
        raise


@mcp.tool()
@django_orm
def update_parameter_template(
    template_id: int,
    name: str | None = None,
    description: str | None = None,
    units: str | None = None,
    checkbox: bool | None = None,
    choices: str | None = None,
    selection_list_id: int | None = None,
    enabled: bool | None = None,
    clear_selection_list: bool = False,
) -> dict[str, Any]:
    """Edit a template without changing its model applicability; omitted fields are preserved.

    clear_selection_list detaches the list and conflicts with selection_list_id.
    choices is a raw comma-separated string. Host save may asynchronously rebuild
    numeric values; this tool does not wait for that work. Choice/unit edits can
    invalidate existing values; rebuilding does not repair invalid choices.
    """
    from common.models import ParameterTemplate, SelectionList
    from django.db import transaction

    if clear_selection_list and selection_list_id is not None:
        raise ValueError("selection_list_id and clear_selection_list are mutually exclusive")
    changes: dict[str, Any] = {
        key: value
        for key, value in {
            "name": name,
            "description": description,
            "units": units,
            "checkbox": checkbox,
            "choices": choices,
            "enabled": enabled,
        }.items()
        if value is not None
    }
    if not changes and selection_list_id is None and not clear_selection_list:
        raise ValueError("At least one change must be supplied")
    with transaction.atomic():
        template = ParameterTemplate.objects.select_for_update().get(pk=template_id)
        selection = template.selectionlist if template.selectionlist_id is not None else None
        if selection_list_id is not None:
            selection = SelectionList.objects.get(pk=selection_list_id)
            changes["selectionlist"] = selection
        elif clear_selection_list:
            selection = None
            changes["selectionlist"] = None
        _validate_template_options(
            template.checkbox if checkbox is None else checkbox,
            template.choices if choices is None else choices,
            selection,
            attaching=selection_list_id is not None,
        )
        _save_changes(template, changes)
        return _template_row(template, None)
