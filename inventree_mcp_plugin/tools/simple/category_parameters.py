"""Category defaults for parts and category-owned generic parameter values.

Direct ORM access bypasses REST role checks. Locks coordinate these tools only.
"""

from __future__ import annotations

__all__: list[str] = []

from typing import Any

from ...mcp_server import mcp
from ...tools import _project, django_orm
from . import parameters


def _assignment_row(assignment: Any, fields: list[str] | None = None) -> dict[str, Any]:
    return _project(
        {
            "id": assignment.pk,
            "category": assignment.category_id,
            "template": assignment.template_id,
            "default_value": assignment.default_value,
        },
        fields,
    )


def _save_assignment(assignment: Any, template: Any) -> None:
    # Newer hosts skip uniqueness-enforcing templates when copying category defaults.
    if getattr(template, "unique", 0) != 0:
        raise ValueError("Uniqueness-enforcing templates cannot provide category defaults")
    default = assignment.default_value.strip()
    if default:
        parameters._validate_parameter_choices(template, default)
    choices = template.get_choices()
    if default and choices and default not in choices:
        raise ValueError("Default value is not a valid template choice")
    assignment.full_clean()
    assignment.save()


@mcp.tool()
@django_orm
def list_category_parameter_templates(
    category_id: int | None = None,
    template_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List direct category-template default assignments, not inherited mappings or category values.

    Optional category_id/template_id filters. fields: id, category, template, default_value.
    id is always included; limit and offset are nonnegative; ordered by ID.
    """
    from part.models import PartCategoryParameterTemplate

    parameters._validate_paging(limit, offset)
    queryset = PartCategoryParameterTemplate.objects.all()
    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)
    if template_id is not None:
        queryset = queryset.filter(template_id=template_id)
    return [_assignment_row(a, fields) for a in queryset.order_by("pk")[offset : offset + limit]]


@mcp.tool()
@django_orm
def get_category_parameter_template(assignment_id: int, fields: list[str] | None = None) -> dict[str, Any]:
    """Get a default assignment. fields: id, category, template, default_value; id is always included."""
    from part.models import PartCategoryParameterTemplate

    return _assignment_row(PartCategoryParameterTemplate.objects.get(pk=assignment_id), fields)


@mcp.tool()
@django_orm
def create_category_parameter_template(category_id: int, template_id: int, default_value: str = "") -> dict[str, Any]:
    """Assign an enabled generic-or-Part template to a category for defaults copied to parts.

    This is not a category-owned value. Host full_clean validates/normalizes defaults
    (including units); nonblank defaults must match resolved choices when defined.
    Blank defaults are allowed. Duplicates and uniqueness-enforcing templates are rejected.
    """
    from common.models import ParameterTemplate
    from django.contrib.contenttypes.models import ContentType
    from django.db import transaction
    from django.db.utils import IntegrityError
    from part.models import Part, PartCategory, PartCategoryParameterTemplate

    try:
        with transaction.atomic():
            template = ParameterTemplate.objects.select_for_update().get(pk=template_id)
            parameters._validate_template(template, ContentType.objects.get_for_model(Part), creating=True)
            category = PartCategory.objects.get(pk=category_id)
            assignment = PartCategoryParameterTemplate(
                category=category, template=template, default_value=default_value
            )
            _save_assignment(assignment, template)
            return _assignment_row(assignment)
    except IntegrityError as exc:
        if PartCategoryParameterTemplate.objects.filter(category_id=category_id, template_id=template_id).exists():
            raise ValueError("This category already has an assignment for this template") from exc
        raise


@mcp.tool()
@django_orm
def update_category_parameter_template(assignment_id: int, default_value: str) -> dict[str, Any]:
    """Edit only a category assignment's default; no reparenting. Disabled templates remain editable.

    The template must still apply to parts and must not enforce parameter value uniqueness.
    Host validation/normalization of defaults applies.
    """
    from common.models import ParameterTemplate
    from django.contrib.contenttypes.models import ContentType
    from django.db import transaction
    from part.models import Part, PartCategoryParameterTemplate

    with transaction.atomic():
        initial = PartCategoryParameterTemplate.objects.get(pk=assignment_id)
        template = ParameterTemplate.objects.select_for_update().get(pk=initial.template_id)
        parameters._validate_template(template, ContentType.objects.get_for_model(Part), creating=False)
        assignment = PartCategoryParameterTemplate.objects.select_for_update().get(
            pk=assignment_id, template_id=template.pk
        )
        assignment.template = template
        assignment.default_value = default_value
        _save_assignment(assignment, template)
        return _assignment_row(assignment)


@mcp.tool()
@django_orm
def list_category_parameters(
    category_id: int,
    template_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """List a category's own generic parameter values, not defaults for parts or ancestors' values.

    Category must exist. fields: id, category, template, name, units, data, data_numeric, note.
    id always included; limit/offset nonnegative; results ordered by ID.
    """
    from common.models import Parameter
    from django.contrib.contenttypes.models import ContentType
    from part.models import PartCategory

    parameters._validate_paging(limit, offset)
    category = PartCategory.objects.get(pk=category_id)
    queryset = Parameter.objects.filter(
        model_type=ContentType.objects.get_for_model(PartCategory), model_id=category.pk
    )
    if template_id is not None:
        queryset = queryset.filter(template_id=template_id)
    queryset = queryset.select_related("template").order_by("pk")
    return [parameters._parameter_row(p, fields, resource="category") for p in queryset[offset : offset + limit]]


@mcp.tool()
@django_orm
def get_category_parameter(parameter_id: int, fields: list[str] | None = None) -> dict[str, Any]:
    """Get a category-owned parameter by ID, scoped to the PartCategory content type.

    fields: id, category, template, name, units, data, data_numeric, note; id always included.
    """
    from common.models import Parameter
    from django.contrib.contenttypes.models import ContentType
    from part.models import PartCategory

    parameter = (
        Parameter.objects
        .filter(model_type=ContentType.objects.get_for_model(PartCategory))
        .select_related("template")
        .get(pk=parameter_id)
    )
    return parameters._parameter_row(parameter, fields, resource="category")


@mcp.tool()
@django_orm
def create_category_parameter(category_id: int, template_id: int, data: str, note: str = "") -> dict[str, Any]:
    """Create a category's own parameter using an enabled generic-or-PartCategory template.

    Category must exist. Host validation applies; duplicates are rejected, never upserted.
    """
    from part.models import PartCategory

    return parameters._create_parameter(PartCategory, "category", category_id, template_id, data, note)


@mcp.tool()
@django_orm
def update_category_parameter(parameter_id: int, data: str | None = None, note: str | None = None) -> dict[str, Any]:
    """Edit a category-owned value, preserving omitted fields; no reparenting.

    Requires data or note. Existing disabled templates may be edited, but must apply
    to categories. Template then parameter locks coordinate these tools only.
    """
    from part.models import PartCategory

    return parameters._update_parameter(PartCategory, "category", parameter_id, data, note)
