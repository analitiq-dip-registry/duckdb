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
  - `database_path` (required) — local path to the `.duckdb`/`.db` file, or `:memory:` for an ephemeral in-memory database. This is the only connection input.
- Driver: `duckdb+duckdb_engine` (synchronous SQLAlchemy transport)
- DSN form: `duckdb+duckdb_engine:///{database_path}` (e.g. `duckdb+duckdb_engine:///:memory:`, `duckdb+duckdb_engine:///relative/file.db`, `duckdb+duckdb_engine:////absolute/path/file.db`). `duckdb_engine` publishes only the bare `duckdb` entry point, so `connector.py` registers the `duckdb.duckdb_engine` alias onto the same dialect class at import time.
- SSH tunnel support: no (there is no network connection to tunnel).

## Post-Auth Steps

None required.

## Available Endpoints

DuckDB is a database connector — it does not ship static endpoint definitions. Schemas and tables are discovered at runtime via the builtin `information_schema` strategy. The system schemas `information_schema` and `pg_catalog` are excluded from discovery.

## Direction

Both. The package registers `DuckDBConnector` under `analitiq.source_connectors` *and* `analitiq.destination_connectors`.

Write path (declared in `definition/connector.json` as `merge_form: insert_on_conflict`, `stage.scope: temp`, `stage.schema: target`, `stage.transactional_ddl: true`, `session_targeting: per_statement`, `catalog: none`, `bulk_load.sqlalchemy: copy_from`):

1. Stage — `CREATE TEMPORARY TABLE <stage> AS SELECT * FROM <target> WHERE 1 = 0` (DuckDB implements no `CREATE TABLE ... LIKE`).
2. Land — the dialect's `bulk_land` registers the Arrow batch on the in-process DuckDB connection under a uuid-suffixed view name and runs one `INSERT ... SELECT` from it (zero-copy native Arrow ingestion), always unregistering in a `finally`. It returns `False` — falling back to the engine's `executemany` landing — only when a DuckDB connection cannot be recovered from the SQLAlchemy handle, and logs why.
3. Apply — the engine's own `INSERT ... SELECT` for append / truncate-insert (preceded by `DELETE FROM <target>` on a truncate-insert's first batch), or `INSERT INTO <target> (...) SELECT ... FROM <stage> ON CONFLICT (keys) DO UPDATE SET ...` for upsert (degrading to `DO NOTHING` when every landed column is a conflict key). Then the stage is dropped.

## Type Maps

Two files, one per direction (the old combined `definition/type-map.json` has been removed):

- `definition/type-map-read.json` — DuckDB native type → canonical, used on discovery and reads.
- `definition/type-map-write.json` — canonical → DuckDB `CREATE TABLE` type, used when the destination renders a table.

## Rate Limits

Not applicable — DuckDB runs in-process against a local file; there is no network API or request quota. Concurrency is constrained by DuckDB's single-writer model (one read-write connection per database file).

## Caveats

- **No read-only mode.** The connector always opens the database read-write. A `read_only` checkbox was declared until v0.1.0 but was bound to nothing — the DSN template interpolates only `database_path` — and would not have worked as spelled in any case: `duckdb_engine` forwards URL query parameters as DuckDB *config keys*, and `read_only` is not one (`?read_only=true` raises `Catalog Error: unrecognized configuration parameter "read_only"`). The key that does open a database read-only is `access_mode=READ_ONLY`. The inert input was removed rather than rewired: this package registers as a **destination** connector as well as a source, and `READ_ONLY` breaks every write with no declaration-level signal to the engine.
- **Single writer.** A DuckDB file supports one read-write connection at a time; the connector uses `pool_size: 1`. There is no supported way to attach to a file another process holds open read-write.
- **In-memory databases are ephemeral.** `:memory:` data is lost when the connection closes and is not shared across connections.
- **Absolute vs relative paths.** Absolute paths need four slashes after the scheme (`duckdb+duckdb_engine:////abs/path`); relative paths use three (`duckdb+duckdb_engine:///rel/path`). The full path is supplied verbatim in `database_path`.
- **`config` passthrough not exposed.** DuckDB's `config` dict (e.g. `threads`, `memory_limit`) is not currently a connection input — the connection-contract input types do not represent dictionaries.
- **Sessions are pinned to UTC.** `session_init_sql` issues `SET TimeZone = 'UTC'` so `TIMESTAMP WITH TIME ZONE` renders the stored instant regardless of the host zone, matching the read map's `Timestamp(MICROSECOND, UTC)` canonical.
- **`catalog: none`.** DuckDB can resolve three-part `catalog.schema.table` names, but only for `ATTACH`-ed databases; this connector opens exactly one file and exposes no ATTACH input, so catalog addressing is declared absent.
- **`max_identifier_len: 63`.** DuckDB itself imposes no limit, but `duckdb_engine` subclasses SQLAlchemy's `PGDialect`, whose `max_identifier_length` is 63 — the budget generated labels are truncated within on the read path.
- **Upsert requires a constraint.** DuckDB resolves an `ON CONFLICT` target only against a real `UNIQUE`/`PRIMARY KEY` constraint or index; an upsert into a target without one fails loudly.
- **Read type mapping notes.** `HUGEINT`/`UHUGEINT` (128-bit) map to `Decimal128(38, 0)` (no 128-bit Arrow integer primitive); nested types (`LIST`, `STRUCT`, `MAP`, `UNION`, `ARRAY`) and `JSON` map to `Json`; `INTERVAL` maps to `Duration(MICROSECOND)` (month/day components are approximate); `UUID` and `ENUM` map to `Utf8`.
- **Write type mapping notes.** `LargeUtf8` and `Null` render `VARCHAR`; `Json`/`Object`/`List` render `JSON`; decimals with precision above DuckDB's maximum of 38 fall back to `VARCHAR` rather than truncating.
- **pyarrow is not a declared dependency.** `requirements.txt` lists only `duckdb`, `duckdb_engine` and `pytz`; the CDK owns the pyarrow pin and supplies the Arrow batches, so `bulk_land` imports pyarrow lazily and the module imports cleanly without it.
