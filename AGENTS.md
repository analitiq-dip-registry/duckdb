---
name: duckdb
description: >
  DuckDB — an embedded, in-process OLAP SQL database accessed via the duckdb_engine SQLAlchemy dialect.
type: database
---

# DuckDB

DuckDB is an embedded, in-process analytical (OLAP) SQL database. This connector reads from and writes to a local DuckDB database file (or an ephemeral in-memory database) through the `duckdb_engine` SQLAlchemy dialect. DuckDB is not a client-server system — there is no network host, port, or login; access is governed entirely by local filesystem permissions.

## Authentication

### None (filesystem-governed)
- Client app required: no
- DuckDB has no username, password, or TLS handshake. Access to a database is controlled by the operating system's filesystem permissions on the database file.
- Connection inputs:
  - `database_path` (required) — local path to the `.duckdb`/`.db` file, or `:memory:` for an ephemeral in-memory database.
  - `read_only` (optional, default `false`) — open an existing file read-only; required for concurrent multi-process access.
- DSN form: `duckdb:///{database_path}` (e.g. `duckdb:///:memory:`, `duckdb:///relative/file.db`, `duckdb:////absolute/path/file.db`).

## Post-Auth Steps

None required.

## Available Endpoints

DuckDB is a database connector — it does not ship static endpoint definitions. Schemas and tables are discovered at runtime via the builtin `information_schema` strategy. The system schemas `information_schema` and `pg_catalog` are excluded from discovery.

## Rate Limits

Not applicable — DuckDB runs in-process against a local file; there is no network API or request quota. Concurrency is constrained by DuckDB's single-writer model (one read-write connection per database file).

## Caveats

- **Single writer.** A DuckDB file supports one read-write connection at a time; the connector uses `pool_size: 1`. Use `read_only: true` for concurrent multi-process reads.
- **In-memory databases are ephemeral.** `:memory:` data is lost when the connection closes and is not shared across connections.
- **Absolute vs relative paths.** Absolute paths need four slashes after the scheme (`duckdb:////abs/path`); relative paths use three (`duckdb:///rel/path`). The full path is supplied verbatim in `database_path`.
- **`config` passthrough not exposed.** DuckDB's `config` dict (e.g. `threads`, `memory_limit`) is not currently a connection input — the connection-contract input types do not represent dictionaries.
- **Type mapping notes.** `HUGEINT`/`UHUGEINT` (128-bit) map to `Decimal128(38, 0)` (no 128-bit Arrow integer primitive); nested types (`LIST`, `STRUCT`, `MAP`, `UNION`, `ARRAY`) and `JSON` map to `Json`; `INTERVAL` maps to `Duration(MICROSECOND)` (month/day components are approximate); `UUID` and `ENUM` map to `Utf8`.
