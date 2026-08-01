"""DuckDB connector - dialect + connector class for the Analitiq CDK.

Everything DuckDB-specific lives here: the ``duckdb+duckdb_engine``
SQLAlchemy dialect registration, the stage-then-merge write hooks
(``CREATE TEMPORARY TABLE ... AS SELECT ... WHERE 1 = 0`` for the stage,
``INSERT ... SELECT ... ON CONFLICT ... DO UPDATE`` for the upsert, a bare
``DELETE FROM`` for the truncate step), the UTC session pin, and the
native Arrow bulk-landing hook. Column types for the write direction are
governed entirely by ``definition/type-map-write.json``; this module ships
no Python type-rendering table.

Transport: synchronous SQLAlchemy ``duckdb+duckdb_engine``. DuckDB is an
embedded, in-process engine - there is no wire protocol, no host/port, no
credentials and no TLS - so the connector declares ``auth.type: none``,
no TLS block, and none of the TLS dialect hooks. There is no ADBC DuckDB
driver in the engine's shipped set, so the decision order stops at the
SQLAlchemy tier.

**Dialect registration.** ``duckdb_engine`` publishes exactly ONE
``sqlalchemy.dialects`` entry point, the bare name ``duckdb`` - even
though the dialect class itself declares ``name = "duckdb"`` and
``driver = "duckdb_engine"``. SQLAlchemy resolves a ``dialect+driver``
URL through the entry-point name ``duckdb.duckdb_engine``, which nothing
publishes, so ``create_engine("duckdb+duckdb_engine://...")`` raises
``NoSuchModuleError`` on a stock install. The contract requires the
explicit ``dialect+driver`` form (it is how a reader sees the sync/async
choice) and requires that the named driver be a real registration, so
this module makes it real at import time with SQLAlchemy's public
``registry.register``. The connector class is entry-point-resolved out of
this module, so the registration is always in place before anything can
build the transport.

**Writes** ride the CDK's stage-then-apply cycle (ADR sql-write-path-v2):
create the stage shaped like the target (``stage_table_sql``), fill it
(``bulk_land``), optionally empty the target on a truncate-insert's first
batch (``empty_table_sql``), apply stage to target with the mode
statement - the engine's own ``INSERT ... SELECT`` for append /
truncate-insert, or this dialect's ``merge_statement_sql`` for upsert -
then drop the stage. ``definition/connector.json`` declares that shape:
``merge_form: insert_on_conflict``, ``stage.scope: temp``,
``stage.schema: target``, ``stage.transactional_ddl: true``,
``session_targeting: per_statement``, ``catalog: none``, and
``bulk_load.sqlalchemy: copy_from``.

**catalog: none.** DuckDB *can* resolve three-part ``catalog.schema.table``
names, but only for databases the session has ``ATTACH``-ed, and this
connector's connection contract opens exactly one database file and
exposes no ATTACH input. The single catalog's name is an artifact of the
file name (``memory`` for ``:memory:``), not something a destination can
name or the engine can create - so, exactly as for PostgreSQL, a
connection sees one database and catalog addressing is declared absent.
The dialect leaves ``supports_catalog_addressing`` at the base default so
the two channels agree.

**max_identifier_len: 63.** DuckDB itself imposes no identifier-length
limit (a 300-character table name is accepted), but ``duckdb_engine``
subclasses SQLAlchemy's ``PGDialect``, whose ``max_identifier_length`` is
63 - and that is the budget SQLAlchemy Core truncates generated labels
within on the read path this connector compiles through. 63 is therefore
the honest declared cap, and it is what ``SqlDialect.max_identifier_length``
already is, so the declaration and the class attribute agree without an
override.

Registered under connector_id ``duckdb`` via the package entry points
(``analitiq.source_connectors`` / ``analitiq.destination_connectors``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.dialects import registry as _sa_dialect_registry

from cdk.sql.dialects import SqlDialect, TableAddress
from cdk.sql.generic import GenericSQLConnector

logger = logging.getLogger(__name__)

# duckdb_engine ships only the bare `duckdb` entry point; the connector's
# declared transport driver is the explicit `duckdb+duckdb_engine` form,
# which SQLAlchemy looks up as `duckdb.duckdb_engine`. Register the alias
# onto the same dialect class the bare name resolves to, so both spellings
# load the one dialect. `registry.register` is idempotent (it overwrites
# the same key with the same target), so a re-import is harmless.
_sa_dialect_registry.register("duckdb.duckdb_engine", "duckdb_engine", "Dialect")

#: Prefix for the per-call Arrow view ``bulk_land`` registers on the
#: in-process connection. The suffix is a fresh uuid4 so two concurrent
#: batches on the same connection can never collide on the name.
_ARROW_VIEW_PREFIX = "__analitiq_arrow_"

#: The DuckDB connection surface ``bulk_land`` needs. ``conn`` may arrive as
#: a SQLAlchemy ``Connection``, a pooled DBAPI proxy, ``duckdb_engine``'s
#: ``ConnectionWrapper`` (which delegates every unknown attribute to the
#: underlying handle), or the raw ``DuckDBPyConnection``; the first object
#: in that chain exposing all three names is the one to drive.
_DUCKDB_CONNECTION_API = ("register", "unregister", "execute")

#: Attributes SQLAlchemy exposes to step one level closer to the DBAPI
#: handle, most specific first.
_UNWRAP_ATTRS = ("driver_connection", "dbapi_connection", "connection")


def _duckdb_connection(conn: Any) -> Any | None:
    """Walk *conn* down to the object that speaks DuckDB's Python API.

    Returns ``None`` rather than raising when no such object is found:
    ``bulk_land``'s contract is "did I land the batch", and a ``False``
    answer degrades to the engine's executemany landing, which is always
    correct. A structural surprise here should cost throughput, not
    correctness - the caller logs it so the degradation is never silent.
    """
    candidate = conn
    seen: set[int] = set()
    while candidate is not None and id(candidate) not in seen:
        seen.add(id(candidate))
        if all(
            callable(getattr(candidate, name, None))
            for name in _DUCKDB_CONNECTION_API
        ):
            return candidate
        candidate = next(
            (
                nxt
                for nxt in (getattr(candidate, a, None) for a in _UNWRAP_ATTRS)
                if nxt is not None
            ),
            None,
        )
    return None


class DuckDBDialect(SqlDialect):
    """DuckDB SQL strategy: CTAS staging, ON CONFLICT upserts, Arrow landing."""

    name = "duckdb"

    #: DuckDB's information_schema and its PostgreSQL compatibility catalog.
    #: Both live in the read-only ``system`` catalog; ``main`` is deliberately
    #: absent because it is the DEFAULT USER schema of the opened database.
    system_schemas = ("information_schema", "pg_catalog")

    # ---- session state -------------------------------------------------------
    def session_init_sql(self) -> list[str]:
        """Pin the session to UTC.

        DuckDB stores TIMESTAMP WITH TIME ZONE as a UTC instant but renders
        and parses it through the session's ``TimeZone`` setting, which
        defaults to the host's local zone. Without this pin the same stored
        instant would come back as a different wall clock on a differently
        configured runner, and the read map's ``Timestamp(MICROSECOND, UTC)``
        canonical would name that wall clock rather than the instant.
        """
        return ["SET TimeZone = 'UTC'"]

    # ---- stage-then-merge write path ---------------------------------------
    def stage_table_sql(
        self, stage: TableAddress, target: TableAddress, *, temp: bool
    ) -> str:
        """``CREATE [TEMPORARY] TABLE`` *stage* shaped like *target*.

        DuckDB implements no ``CREATE TABLE ... LIKE`` (its parser raises
        "ColumnDef type not handled yet"), so the stage is cloned with an
        empty CTAS: ``AS SELECT * FROM target WHERE 1 = 0`` reproduces the
        target's column names and types in order and lands no rows. What
        CTAS does NOT copy - constraints and DEFAULT expressions - a stage
        does not need: the landing INSERT binds only the columns the batch
        carries, and the mode statement selects only those same columns
        from the stage, so every unlanded target column takes its own
        DEFAULT on the target side.

        Under the connector's declared ``stage.scope: temp`` the CDK hands
        this hook a schema-less stage address, so ``quote_table`` renders a
        bare identifier - which is what DuckDB requires, since a TEMPORARY
        table lives in the session-private ``temp`` catalog and cannot be
        schema-qualified into the target's schema. The whole cycle runs on
        one connection, so the temporary table is visible to every step and
        disappears with the session even if a cycle dies between create and
        drop.
        """
        create = "CREATE TEMPORARY TABLE" if temp else "CREATE TABLE"
        return (
            f"{create} {self.quote_table(stage)} AS "
            f"SELECT * FROM {self.quote_table(target)} WHERE 1 = 0"
        )

    def merge_statement_sql(
        self,
        stage: TableAddress,
        target: TableAddress,
        conflict_keys: Sequence[str],
        columns: Sequence[str],
    ) -> str:
        """Render the upsert from *stage* to *target*.

        DuckDB's merge grammar is PostgreSQL's ``INSERT ... ON CONFLICT
        (keys) DO UPDATE SET col = EXCLUDED.col``, which is what the
        connector declares (``merge_form: insert_on_conflict``). DuckDB
        1.4 added a SQL-standard ``MERGE``, but ON CONFLICT is the form
        every supported release speaks, so it is the portable choice.

        The source is the stage table, referenced once, so no batch value
        is ever rendered into SQL text - values reach the stage as bound
        parameters (or through ``bulk_land``'s Arrow view). Updated
        columns are the landed columns minus the conflict keys: a target
        column the batch did not land keeps its stored value on a matched
        row and takes its DEFAULT on an inserted one.

        When every landed column is a conflict key there is nothing to
        update, and ``DO UPDATE SET`` with an empty list is a syntax
        error, so the contract's insert-only degradation renders ``DO
        NOTHING`` - matched rows are left untouched and no error is
        raised.

        Known precondition, inherited from the grammar: DuckDB resolves an
        ON CONFLICT target only against a real UNIQUE / PRIMARY KEY
        constraint or index ("The specified columns as conflict target are
        not referenced by a UNIQUE/PRIMARY KEY CONSTRAINT or INDEX"), so an
        upsert into a target without one fails loudly rather than silently
        appending duplicates.
        """
        target_ref = self.quote_table(target)
        stage_ref = self.quote_table(stage)
        column_list = ", ".join(self.quote_ident(c) for c in columns)
        conflict_list = ", ".join(self.quote_ident(k) for k in conflict_keys)
        keys = set(conflict_keys)
        update_columns = [c for c in columns if c not in keys]
        if update_columns:
            set_clause = ", ".join(
                f"{self.quote_ident(c)} = EXCLUDED.{self.quote_ident(c)}"
                for c in update_columns
            )
            action = f"DO UPDATE SET {set_clause}"
        else:
            action = "DO NOTHING"
        # Dialect-quoted identifiers only; batch values never enter this
        # text (they reach the stage as bound parameters / an Arrow view).
        return (
            f"INSERT INTO {target_ref} ({column_list}) "  # nosec B608
            f"SELECT {column_list} FROM {stage_ref} "
            f"ON CONFLICT ({conflict_list}) {action}"
        )

    def empty_table_sql(self, target: TableAddress) -> str:
        """Empty *target* before a truncate-insert's first batch.

        A bare ``DELETE FROM t`` is chosen over ``TRUNCATE`` deliberately.
        Both are transactional in DuckDB (a rolled-back TRUNCATE restores
        the rows), so the choice is not about atomicity; ``DELETE`` is the
        form whose semantics are identical across every engine the CDK
        drives, it needs no WHERE clause here (unlike BigQuery), and it
        keeps the emptying step inside the same MVCC transaction as the
        stage cycle without depending on TRUNCATE's engine-specific
        commit behaviour.
        """
        return f"DELETE FROM {self.quote_table(target)}"

    def bulk_land(
        self,
        conn: Any,
        stage: TableAddress,
        batch: Any,
        *,
        runtime: Any,
    ) -> bool:
        """Fill the freshly-created *stage* with *batch* via native Arrow.

        DuckDB runs in-process and consumes Arrow directly: registering the
        batch on the connection exposes it as a zero-copy relation, and
        ``INSERT ... SELECT`` from that relation moves the whole batch in
        one statement, columnar all the way. That is what the connector
        declares as ``bulk_load.sqlalchemy: copy_from`` - the contract's
        dialect-implemented, non-executemany landing family, realized here
        as DuckDB's own Arrow scan rather than a file-based ``COPY``, since
        an embedded engine has no wire connection to copy over and no
        intermediate file to write.

        The registration is per call and always torn down: the view name
        carries a fresh uuid4, and ``unregister`` runs in a ``finally`` so
        a failed INSERT cannot leave a dangling name (or a reference
        pinning the batch's buffers) on a pooled connection.

        Only the batch's own columns are named on both sides of the
        INSERT, so a stage cloned with every target column simply leaves
        the unlanded ones NULL - the mode statement never selects them.

        *batch* is an Arrow ``RecordBatch`` (typed ``Any`` because pyarrow
        is imported lazily, below, rather than at module scope). This
        connector's ``requirements.txt`` deliberately does not list
        pyarrow: the CDK owns that pin, and a batch only ever reaches this
        hook because the engine built it with the CDK's own pyarrow. Doing
        the import here rather than at module scope keeps that ownership
        honest - the module, and therefore entry-point resolution of
        ``DuckDBConnector``, imports cleanly even in an environment where
        pyarrow is absent, and the dependency is only asserted on the code
        path that genuinely needs it.

        *runtime* is unused: everything needed is on *conn* and *batch*.
        Returns ``True`` when the batch landed. Returns ``False`` only
        when the DuckDB connection cannot be recovered from *conn*, which
        makes the engine fall back to its executemany landing (correct,
        just slower); the reason is logged so the degradation is visible.
        Anything else - a bad cast, a constraint failure - propagates, so
        the engine classifies and acks it.
        """
        del runtime  # everything needed is on `conn` / `batch`

        duck = _duckdb_connection(conn)
        if duck is None:
            logger.warning(
                "duckdb: could not recover a DuckDB connection from %s; "
                "falling back to the engine's executemany landing",
                type(conn).__name__,
            )
            return False
        if batch.num_rows == 0:
            return True

        # Lazy on purpose (see the docstring): the CDK owns the pyarrow
        # pin and hands this hook the batch, so pyarrow is present
        # whenever this line runs - but module import must not depend on
        # it.
        import pyarrow as pa

        # RecordBatch -> Table is a zero-copy wrap; going through it keeps
        # the call working across duckdb releases whose `register` accepts
        # only table-like Arrow objects.
        relation = (
            pa.Table.from_batches([batch])
            if isinstance(batch, pa.RecordBatch)
            else batch
        )
        view = f"{_ARROW_VIEW_PREFIX}{uuid.uuid4().hex}"
        column_list = ", ".join(self.quote_ident(c) for c in batch.schema.names)
        duck.register(view, relation)
        try:
            # Identifiers only: the column names come from the batch schema
            # and the view name is generated here; the row values never
            # enter the SQL text, they are read out of the Arrow buffers.
            duck.execute(  # nosec B608
                f"INSERT INTO {self.quote_table(stage)} ({column_list}) "
                f"SELECT {column_list} FROM {self.quote_ident(view)}"
            )
        finally:
            duck.unregister(view)
        return True


class DuckDBConnector(GenericSQLConnector):
    """DuckDB connector: the CDK SQL base wired to the DuckDB dialect."""

    dialect_class = DuckDBDialect
