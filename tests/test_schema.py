"""Schema snapshot tests for registered MCP tools.

Verifies that all expected tools are registered with the correct names,
non-empty descriptions, and required parameters. These tests run without
InvenTree and catch accidental renames, removals, or signature changes.
"""

from __future__ import annotations

import pytest

# Tools: name -> set of required parameter names (non-defaulted).
_EXPECTED_TOOLS: dict[str, set[str]] = {
    # Parts
    "list_parts": set(),
    "get_part": {"part_id"},
    "search_parts": {"query"},
    "create_part": {"name", "description", "category_id"},
    "update_part": {"part_id"},
    # Parameters
    "list_parameter_templates": set(),
    "get_parameter_template": {"template_id"},
    "list_part_parameters": {"part_id"},
    "get_part_parameter": {"parameter_id"},
    "create_part_parameter": {"part_id", "template_id", "data"},
    "update_part_parameter": {"parameter_id"},
    "create_parameter_template": {"name"},
    "update_parameter_template": {"template_id"},
    "list_selection_lists": set(),
    "get_selection_list": {"selection_list_id"},
    "create_selection_list": {"name"},
    "update_selection_list": {"selection_list_id"},
    "list_selection_list_entries": {"selection_list_id"},
    "get_selection_list_entry": {"entry_id"},
    "create_selection_list_entry": {"selection_list_id", "value", "label"},
    "update_selection_list_entry": {"entry_id"},
    "list_category_parameter_templates": set(),
    "get_category_parameter_template": {"assignment_id"},
    "create_category_parameter_template": {"category_id", "template_id"},
    "update_category_parameter_template": {"assignment_id", "default_value"},
    "list_category_parameters": {"category_id"},
    "get_category_parameter": {"parameter_id"},
    "create_category_parameter": {"category_id", "template_id", "data"},
    "update_category_parameter": {"parameter_id"},
    # Stock
    "list_stock_items": set(),
    "get_stock_item": {"stock_item_id"},
    "adjust_stock": {"stock_item_id", "quantity"},
    "transfer_stock": {"stock_item_id", "location_id"},
    # Locations
    "list_locations": set(),
    "get_location": {"location_id"},
    "get_location_tree": set(),
    # Categories
    "list_categories": set(),
    "get_category": {"category_id"},
    "get_category_tree": set(),
    # Orders
    "list_purchase_orders": set(),
    "get_purchase_order": {"order_id"},
    "list_sales_orders": set(),
    "get_sales_order": {"order_id"},
    # BOM
    "list_bom_items": set(),
    "get_bom_for_part": {"part_id"},
    # Builds
    "list_build_orders": set(),
    "get_build_order": {"build_id"},
    # Tags
    "list_tags": set(),
    "search_tags": {"query"},
    # Combinatory
    "delete_parts": {"part_ids"},
    "stock_by_category_and_location": set(),
    "stock_pivot": set(),
}


@pytest.fixture(scope="module")
def registered_tools() -> dict[str, object]:
    """Import the MCP server and return {name: Tool} for all registered tools.

    Scoped to the module so the import only happens once per test session.
    The autouse stub fixture sets up sys.modules before the first test in
    this module runs, so Django/InvenTree imports are satisfied.
    """
    from inventree_mcp_plugin.mcp_server import mcp

    return {t.name: t for t in mcp._tool_manager.list_tools()}


class TestToolRegistry:
    @pytest.mark.parametrize(
        "name,properties",
        [
            (
                "create_parameter_template",
                "name description units checkbox choices selection_list_id enabled model_type",
            ),
            (
                "update_parameter_template",
                "template_id name description units checkbox choices selection_list_id enabled clear_selection_list",
            ),
            ("list_selection_lists", "search active limit offset fields"),
            ("get_selection_list", "selection_list_id fields"),
            ("create_selection_list", "name description active"),
            ("update_selection_list", "selection_list_id name description active default_entry_id clear_default"),
            ("list_selection_list_entries", "selection_list_id active limit offset fields"),
            ("get_selection_list_entry", "entry_id fields"),
            ("create_selection_list_entry", "selection_list_id value label description active"),
            ("update_selection_list_entry", "entry_id value label description active"),
            ("list_category_parameter_templates", "category_id template_id limit offset fields"),
            ("get_category_parameter_template", "assignment_id fields"),
            ("create_category_parameter_template", "category_id template_id default_value"),
            ("update_category_parameter_template", "assignment_id default_value"),
            ("list_category_parameters", "category_id template_id limit offset fields"),
            ("get_category_parameter", "parameter_id fields"),
            ("create_category_parameter", "category_id template_id data note"),
            ("update_category_parameter", "parameter_id data note"),
        ],
    )
    def test_resource_signatures(self, name: str, properties: str, registered_tools: dict) -> None:
        schema = registered_tools[name].parameters
        assert set(schema["properties"]) == set(properties.split())
        assert set(schema.get("required", [])) == _EXPECTED_TOOLS[name]
        for key, prop in schema["properties"].items():
            types = {p["type"] for p in prop.get("anyOf", [prop])}
            expected = "integer" if key.endswith("_id") or key in {"limit", "offset"} else "string"
            if key in {"active", "enabled", "checkbox", "clear_default", "clear_selection_list"}:
                expected = "boolean"
            elif key == "fields":
                expected = "array"
            assert expected in types
            if key not in _EXPECTED_TOOLS[name]:
                assert "default" in prop
        if "limit" in schema["properties"]:
            assert schema["properties"]["limit"]["default"] == 100
            assert schema["properties"]["offset"]["default"] == 0
        for key in {"clear_default", "clear_selection_list"} & schema["properties"].keys():
            assert schema["properties"][key]["default"] is False

    @pytest.mark.parametrize(
        "name,properties,required,defaults",
        [
            (
                "list_parameter_templates",
                {"search", "limit", "offset", "fields", "model_type", "enabled"},
                set(),
                {"search": None, "limit": 100, "offset": 0, "fields": None, "model_type": "part.part", "enabled": True},
            ),
            ("get_parameter_template", {"template_id", "fields"}, {"template_id"}, {"fields": None}),
            (
                "list_part_parameters",
                {"part_id", "template_id", "limit", "offset", "fields"},
                {"part_id"},
                {"template_id": None, "limit": 100, "offset": 0, "fields": None},
            ),
            ("get_part_parameter", {"parameter_id", "fields"}, {"parameter_id"}, {"fields": None}),
            (
                "create_part_parameter",
                {"part_id", "template_id", "data", "note"},
                {"part_id", "template_id", "data"},
                {"note": ""},
            ),
            ("update_part_parameter", {"parameter_id", "data", "note"}, {"parameter_id"}, {"data": None, "note": None}),
        ],
    )
    def test_parameter_signatures(
        self, name: str, properties: set[str], required: set[str], defaults: dict, registered_tools: dict
    ) -> None:
        schema = registered_tools[name].parameters
        assert set(schema["properties"]) == properties
        assert set(schema.get("required", [])) == required
        for key, value in defaults.items():
            assert schema["properties"][key]["default"] == value
        for key in properties & {"part_id", "template_id", "parameter_id", "limit", "offset"}:
            prop = schema["properties"][key]
            if key == "template_id" and name == "list_part_parameters":
                assert prop["anyOf"] == [{"type": "integer"}, {"type": "null"}]
            else:
                assert prop["type"] == "integer"
        for key in properties & {"data", "note", "search"}:
            prop = schema["properties"][key]
            if defaults.get(key, "") is None:
                assert prop["anyOf"] == [{"type": "string"}, {"type": "null"}]
            else:
                assert prop["type"] == "string"
        if "fields" in properties:
            assert schema["properties"]["fields"]["anyOf"] == [
                {"items": {"type": "string"}, "type": "array"},
                {"type": "null"},
            ]

    def test_total_tool_count(self, registered_tools: dict) -> None:
        assert len(registered_tools) == len(_EXPECTED_TOOLS), (
            f"Expected {len(_EXPECTED_TOOLS)} tools, got {len(registered_tools)}.\n"
            f"Extra:   {set(registered_tools) - set(_EXPECTED_TOOLS)}\n"
            f"Missing: {set(_EXPECTED_TOOLS) - set(registered_tools)}"
        )

    def test_all_expected_names_registered(self, registered_tools: dict) -> None:
        missing = set(_EXPECTED_TOOLS) - set(registered_tools)
        assert not missing, f"Tools missing from registry: {sorted(missing)}"

    def test_no_unexpected_tools_registered(self, registered_tools: dict) -> None:
        extra = set(registered_tools) - set(_EXPECTED_TOOLS)
        assert not extra, f"Unexpected tools found in registry: {sorted(extra)}"

    @pytest.mark.parametrize("name", sorted(_EXPECTED_TOOLS))
    def test_tool_has_description(self, name: str, registered_tools: dict) -> None:
        if name not in registered_tools:
            pytest.skip(f"Tool '{name}' not registered")
        tool = registered_tools[name]
        assert tool.description, f"Tool '{name}' has no description"

    @pytest.mark.parametrize("name,required_params", sorted(_EXPECTED_TOOLS.items()))
    def test_tool_required_params(self, name: str, required_params: set[str], registered_tools: dict) -> None:
        if name not in registered_tools:
            pytest.skip(f"Tool '{name}' not registered")
        tool = registered_tools[name]
        schema_required: set[str] = set(tool.parameters.get("required", []))
        missing = required_params - schema_required
        assert not missing, (
            f"Tool '{name}' is missing required params: {sorted(missing)}. Schema required: {sorted(schema_required)}"
        )
