"""Unit tests for the DuckDB connector definition.

Validates connector.json and type-map.json for structural correctness and
DuckDB-specific invariants without requiring a live database.
"""

import json
import pathlib

import pytest

_DEFINITION_DIR = pathlib.Path(__file__).parent.parent / "definition"


def _load(filename: str) -> dict:
    return json.loads((_DEFINITION_DIR / filename).read_text())


@pytest.fixture(scope="module")
def connector() -> dict:
    return _load("connector.json")


@pytest.fixture(scope="module")
def type_map() -> dict:
    return _load("type-map.json")


# ---------------------------------------------------------------------------
# connector.json — identity and structure
# ---------------------------------------------------------------------------


def test_connector_json_is_valid(connector):
    """connector.json parses without error."""
    assert isinstance(connector, dict)


def test_connector_id(connector):
    assert connector["connector_id"] == "duckdb"


def test_connector_kind(connector):
    assert connector["kind"] == "database"


# ---------------------------------------------------------------------------
# Auth — DuckDB has no network credential; access is filesystem-governed.
# ---------------------------------------------------------------------------


def test_auth_type_is_none(connector):
    assert connector["auth"]["type"] == "none"


# ---------------------------------------------------------------------------
# Connection contract
# ---------------------------------------------------------------------------


def test_database_path_is_required(connector):
    inputs = connector["connection_contract"]["inputs"]
    assert "database_path" in inputs
    assert inputs["database_path"]["required"] is True


def test_read_only_is_optional(connector):
    inputs = connector["connection_contract"]["inputs"]
    assert "read_only" in inputs
    assert inputs["read_only"]["required"] is False
    assert inputs["read_only"]["default"] is False


def test_dsn_template_contains_database_path(connector):
    dsn = connector["transports"]["database"]["dsn"]
    assert dsn["kind"] == "url_template"
    assert "{database_path}" in dsn["template"]
    assert dsn["template"].startswith("duckdb:///")


# ---------------------------------------------------------------------------
# write_unit — in-process OLAP engine; large batches are cheap.
# ---------------------------------------------------------------------------


def test_write_unit_declared(connector):
    assert "write_unit" in connector, "write_unit must be declared (issue #8)"


def test_write_unit_rows(connector):
    assert connector["write_unit"]["rows"] > 0


def test_write_unit_bytes(connector):
    assert connector["write_unit"]["bytes"] > 0


# ---------------------------------------------------------------------------
# sql_capabilities — required shape facts + limits
# ---------------------------------------------------------------------------


def test_sql_capabilities_declared(connector):
    assert "sql_capabilities" in connector, "sql_capabilities must be declared (issue #8)"


def test_sql_capabilities_required_shape_facts(connector):
    caps = connector["sql_capabilities"]
    for fact in ("catalog", "session_targeting", "merge_form", "bulk_load", "stage"):
        assert fact in caps, f"sql_capabilities.{fact} is required"


def test_sql_capabilities_merge_form(connector):
    # DuckDB supports standard MERGE syntax.
    assert connector["sql_capabilities"]["merge_form"] == "merge"


def test_sql_capabilities_stage_transactional_ddl(connector):
    # DuckDB DDL is transactional — CREATE TABLE is rollback-safe.
    assert connector["sql_capabilities"]["stage"]["transactional_ddl"] is True


def test_max_bind_params_declared(connector):
    limits = connector["sql_capabilities"].get("limits", {})
    assert "max_bind_params" in limits, (
        "sql_capabilities.limits.max_bind_params must be declared (issue #8)"
    )


def test_max_bind_params_positive(connector):
    assert connector["sql_capabilities"]["limits"]["max_bind_params"] > 0


# ---------------------------------------------------------------------------
# error_map
# ---------------------------------------------------------------------------


def test_error_map_declared(connector):
    assert "error_map" in connector, "error_map must be declared (issue #8)"


def test_error_map_has_exception_or_sqlstate(connector):
    error_map = connector["error_map"]
    assert "exception" in error_map or "sqlstate" in error_map


def test_error_map_exception_values_are_valid_categories(connector):
    valid = {"transient", "config", "auth", "unreachable", "rate_limited", "write_rejected"}
    for exc_class, category in connector["error_map"].get("exception", {}).items():
        assert category in valid, (
            f"error_map.exception[{exc_class!r}] = {category!r} is not a valid failure category"
        )


def test_error_map_sqlstate_values_are_valid_categories(connector):
    valid = {"transient", "config", "auth", "unreachable", "rate_limited", "write_rejected"}
    for code, category in connector["error_map"].get("sqlstate", {}).items():
        assert category in valid, (
            f"error_map.sqlstate[{code!r}] = {category!r} is not a valid failure category"
        )


# ---------------------------------------------------------------------------
# type-map.json
# ---------------------------------------------------------------------------


def test_type_map_is_valid(type_map):
    """type-map.json parses without error and is non-empty."""
    assert isinstance(type_map, (dict, list))
    assert type_map
