"""Unit tests for the DuckDB connector definition.

Validates connector.json and type-map.json for structural correctness and
DuckDB-specific invariants without requiring a live database.
"""

import json
import pathlib
import re

import pytest

_DEFINITION_DIR = pathlib.Path(__file__).parent.parent / "definition"


def _load(filename: str) -> dict | list:
    return json.loads((_DEFINITION_DIR / filename).read_text())


@pytest.fixture(scope="module")
def connector() -> dict:
    return _load("connector.json")


@pytest.fixture(scope="module")
def type_map() -> list:
    return _load("type-map.json")


# ---------------------------------------------------------------------------
# connector.json — identity and structure
# ---------------------------------------------------------------------------


def test_connector_root_is_object(connector):
    """connector.json root is a JSON object, not an array or scalar."""
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


def test_dsn_template_exact(connector):
    """DSN template must be exactly duckdb:///{database_path}.

    Two slashes (duckdb://{database_path}) is the host-based URI form and
    silently produces malformed DSNs for absolute paths.
    """
    dsn = connector["transports"]["database"]["dsn"]
    assert dsn["kind"] == "url_template"
    assert dsn["template"] == "duckdb:///{database_path}"


# ---------------------------------------------------------------------------
# write_unit — in-process OLAP; 64 K rows / 64 MiB targets DuckDB's batch economics.
# ---------------------------------------------------------------------------


def test_write_unit_declared(connector):
    assert "write_unit" in connector, "write_unit must be declared (issue #8)"


def test_write_unit_exact_values(connector):
    assert connector["write_unit"]["rows"] == 32768
    assert connector["write_unit"]["bytes"] == 67108864


def test_write_unit_rows_does_not_exceed_max_bind_params(connector):
    """write_unit.rows must not exceed max_bind_params or batches overflow at bind time."""
    assert (
        connector["write_unit"]["rows"]
        <= connector["sql_capabilities"]["limits"]["max_bind_params"]
    )


# ---------------------------------------------------------------------------
# sql_capabilities — required shape facts + limits
# ---------------------------------------------------------------------------


def test_sql_capabilities_declared(connector):
    assert "sql_capabilities" in connector, "sql_capabilities must be declared (issue #8)"


def test_sql_capabilities_required_shape_facts(connector):
    caps = connector["sql_capabilities"]
    for fact in ("catalog", "session_targeting", "merge_form", "bulk_load", "stage"):
        assert fact in caps, f"sql_capabilities.{fact} is required"


def test_sql_capabilities_catalog_and_session_targeting(connector):
    """DuckDB connects to one file; no catalog prefix. SET applies per-statement."""
    caps = connector["sql_capabilities"]
    assert caps["catalog"] == "none"
    assert caps["session_targeting"] == "per_statement"


def test_sql_capabilities_merge_form(connector):
    # DuckDB supports standard MERGE syntax (available since v1.4.0).
    assert connector["sql_capabilities"]["merge_form"] == "merge"


def test_sql_capabilities_stage(connector):
    """DuckDB supports TEMP tables and DDL is rollback-safe inside transactions."""
    stage = connector["sql_capabilities"]["stage"]
    assert stage["scope"] == "temp"
    assert stage["schema"] == "target"
    assert stage["transactional_ddl"] is True


def test_max_bind_params_exact(connector):
    limits = connector["sql_capabilities"].get("limits", {})
    assert "max_bind_params" in limits, (
        "sql_capabilities.limits.max_bind_params must be declared (issue #8)"
    )
    assert limits["max_bind_params"] == 65535


# ---------------------------------------------------------------------------
# error_map
# ---------------------------------------------------------------------------


def test_error_map_declared(connector):
    assert "error_map" in connector, "error_map must be declared (issue #8)"


def test_error_map_has_exception_and_sqlstate(connector):
    error_map = connector["error_map"]
    assert "exception" in error_map
    assert "sqlstate" in error_map


def test_error_map_sqlstate_keys_are_valid_class_codes(connector):
    """SQLSTATE class codes must be 2-character uppercase-alphanumeric strings."""
    for code in connector["error_map"].get("sqlstate", {}):
        assert re.fullmatch(r"[0-9A-Z]{2,5}", code), (
            f"sqlstate key {code!r} is not a valid SQLSTATE class or code"
        )


def test_error_map_specific_sqlstate_mappings(connector):
    sqlstate = connector["error_map"]["sqlstate"]
    assert sqlstate["08"] == "unreachable"
    assert sqlstate["22"] == "write_rejected"
    assert sqlstate["23"] == "write_rejected"


def test_error_map_specific_exception_mappings(connector):
    exc = connector["error_map"]["exception"]
    assert exc["OperationalError"] == "unreachable"
    assert exc["InterfaceError"] == "unreachable"
    assert exc["IntegrityError"] == "write_rejected"
    assert exc["DataError"] == "write_rejected"


def test_error_map_all_categories_valid(connector):
    valid = {"transient", "config", "auth", "unreachable", "rate_limited", "write_rejected"}
    for code, category in connector["error_map"].get("sqlstate", {}).items():
        assert category in valid, (
            f"error_map.sqlstate[{code!r}] = {category!r} is not a valid failure category"
        )
    for exc_class, category in connector["error_map"].get("exception", {}).items():
        assert category in valid, (
            f"error_map.exception[{exc_class!r}] = {category!r} is not a valid failure category"
        )


# ---------------------------------------------------------------------------
# type-map.json
# ---------------------------------------------------------------------------


def test_type_map_has_entries(type_map):
    assert len(type_map) > 0, "type-map.json must contain at least one mapping entry"


def test_type_map_entry_shape(type_map):
    for i, entry in enumerate(type_map):
        assert "match" in entry, f"entry {i} missing 'match'"
        assert "native" in entry, f"entry {i} missing 'native'"
        assert "canonical" in entry, f"entry {i} missing 'canonical'"
        assert entry["match"] in {"exact", "regex"}, (
            f"entry {i} has invalid match type {entry['match']!r}"
        )


def test_type_map_critical_mappings(type_map):
    """Verify CLAUDE.md-documented special-case mappings are present and correct."""
    exact = {e["native"]: e["canonical"] for e in type_map if e["match"] == "exact"}
    assert exact["HUGEINT"] == "Decimal128(38, 0)"
    assert exact["UHUGEINT"] == "Decimal128(38, 0)"
    assert exact["UUID"] == "Utf8"
    assert exact["INTERVAL"] == "Duration(MICROSECOND)"
    assert exact["JSON"] == "Json"
    assert exact["ENUM"] == "Utf8"
