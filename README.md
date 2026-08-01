# DuckDB

[![Status: unverified](https://img.shields.io/badge/status-unverified-orange)](https://github.com/analitiq-dip-registry)
[![Latest release](https://img.shields.io/github/v/release/analitiq-dip-registry/duckdb)](https://github.com/analitiq-dip-registry/duckdb/releases)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

This connector lets the Analitiq platform read from and write to a **DuckDB** database — a fast, embedded, in-process analytical (OLAP) SQL database. It connects to a local DuckDB database file (or an in-memory database) through the `duckdb_engine` SQLAlchemy dialect.

## What is this?

This is a **connector** — a configuration that defines how to connect to DuckDB and what data is available for reading and writing. It does not move data by itself. Instead, it is used by the [Analitiq](https://analitiq-app.com) data integration platform or the open-source `analitiq-dip-registry` engine to set up data pipelines.

## How to use this connector

There are two ways to use this connector:

### Option 1 — Analitiq Cloud (no setup required)

All connectors from this registry are automatically available on [analitiq-app.com](https://analitiq-app.com). Simply log in, select the connector, and follow the on-screen instructions to connect your database.

### Option 2 — Open Source (self-hosted)

All connectors are open source and free to use. To get started:

1. Clone the [analitiq-dip-registry](https://github.com/analitiq-dip-registry) repository
2. Install the Claude plugin `analitiq-plugin-dataflow`
3. Launch Claude in the root directory of `analitiq-dip-registry`
4. Tell it: *"I need to move data from X to Y"*

The `analitiq-plugin-dataflow` plugin will automatically fetch the required connectors from the [Analitiq DIP Registry](https://github.com/analitiq-dip-registry) and set up the data flow pipeline for you.

## Prerequisites

DuckDB is embedded — there is no server to provision and no account to create. You need:

- A DuckDB database file accessible on the machine where the engine runs (a `.duckdb` or `.db` file), **or** the special value `:memory:` to work against an ephemeral in-memory database.
- Read (and, for writes, write) filesystem permissions on that file and its directory.
- The `duckdb` and `duckdb-engine` Python packages available in the engine's environment.

## Authentication

DuckDB has **no authentication** — no username, password, or TLS. Because DuckDB runs in-process against a local file, access is governed entirely by your operating system's filesystem permissions on the database file. There are no credentials to manage.

### How to connect

You only need to provide the database location:

1. **Database path** (required) — the path to your DuckDB file, for example `/data/analytics.duckdb`, a relative path like `warehouse/local.db`, or `:memory:` for an ephemeral in-memory database.
2. **Access mode** (optional, default Read/Write) — choose **Read-only** to open an existing file without acquiring a write lock. This is required when multiple processes need to read the same file at the same time.

Internally the connection is built as a SQLAlchemy URL of the form `duckdb:///{database_path}?access_mode=READ_WRITE` (or `READ_ONLY`).

## Available Resources

DuckDB is a database connector, so it does not ship a fixed list of endpoints. Instead, the schemas and tables in your database are **discovered automatically at runtime** from `information_schema`. The internal system schemas (`information_schema`, `pg_catalog`) are excluded, so you only see your own data.

## Limitations

- **Single writer** — A DuckDB file supports only one read-write connection at a time. For concurrent access from multiple processes, open the file with **Access mode: Read-only**. The connector uses a single pooled connection.
- **In-memory databases are ephemeral** — When you use `:memory:`, all data is lost once the connection closes, and it is not shared between connections.
- **Absolute vs relative paths** — Absolute file paths require four slashes after the scheme (`duckdb:////absolute/path/file.db`); relative paths use three (`duckdb:///relative/file.db`). Provide the full path in the database-path field.
- **Driver configuration** — Advanced DuckDB `config` options (such as `threads` or `memory_limit`) are not currently exposed as connection settings.
- **Version-dependent types** — DuckDB's available data types depend on the engine version. The connector's type mapping covers the standard general-purpose and nested types; very new or extension-provided types may not be mapped.

## For AI agents

This connector includes `CLAUDE.md` and `AGENTS.md` files — machine-readable references used by AI agents and agentic frameworks. They document the authentication model, connection inputs, resource discovery, and caveats for programmatic use. Both files are kept identical — `CLAUDE.md` is for Claude Code, `AGENTS.md` is for other agent frameworks.

## Create a connector to any system

You can create a new connector to any API or database using Claude and the Analitiq connector builder plugin:

1. Install [Claude Code](https://claude.ai/code)
2. Install the connector builder plugin:
   ```
   claude plugin add analitiq-dip-registry/analitiq-plugin-connector-builder
   ```
3. Launch Claude and say: *"I want to create a connector for [system name]"*
4. The plugin will interview you about the system, research its documentation, and generate the full connector with all required files

No coding required — the plugin handles authentication research, schema generation, and file creation automatically.

![Example of Claude building a connector](media/example_1.png)

## Contributing

All connectors in this registry are community-maintained and live at [github.com/analitiq-dip-registry](https://github.com/analitiq-dip-registry). To improve this connector, install the [connector builder plugin](https://github.com/analitiq-dip-registry/analitiq-plugin-connector-builder) and follow its instructions.

## Links

- [DuckDB Documentation](https://duckdb.org/docs/)
- [duckdb_engine (SQLAlchemy dialect)](https://github.com/Mause/duckdb_engine)
- [Analitiq Cloud](https://analitiq-app.com)
- [Analitiq Engine (open source)](https://github.com/analitiq-ai/analitiq-engine)
- [Analitiq DIP Registry (open source)](https://github.com/analitiq-dip-registry)
