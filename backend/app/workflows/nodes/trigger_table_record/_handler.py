"""`trigger.table_record`: a workflow a table record's creation, change or deletion starts.

A record added to the table, changed or deleted - in the console, over the API,
by an agent or by another workflow - that matches every filter starts one run
with the record, for each event the trigger listens for. A change starts it only
when one of the table's chosen columns changed, when columns are chosen.
Publishing a version is what subscribes the workflow; nothing done before that
starts it.
"""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import AuthContext
from app.repositories import virtual_table_repo
from app.schemas.virtual_table import ColumnDef
from app.services.virtual_tables.types import COLUMN_TYPES, CellProblem, validate_filter
from app.workflows.contracts.definition import Port
from app.workflows.contracts.payload_schema import typed_trigger_output
from app.workflows.contracts.results import Completed, Failed, NodeResult
from app.workflows.nodes._tables import check_table
from app.workflows.nodes._triggers import (
    TableRecordTriggerConfig,
    TableRecordTriggerOutput,
    run_input_as,
)

# What a declared payload field may claim for each column type: the readable
# value `fields` carries for the column. A date column's value is a
# `YYYY-MM-DD` string, so it may be declared `date` or `text`; a datetime's
# is an ISO timestamp, so only `text`; a single select's is its option label,
# so `choice` or `text`. A column type not listed here - a link, a lookup, a
# worked-out value - is checked by name only: its value's shape is not one
# scalar the declaration can promise.
_COLUMN_PAYLOAD_TYPES: dict[str, frozenset[str]] = {
    "text": frozenset({"text"}),
    "long_text": frozenset({"text"}),
    "rich_text": frozenset({"text"}),
    "email": frozenset({"text"}),
    "url": frozenset({"text"}),
    "phone": frozenset({"text"}),
    "number": frozenset({"number"}),
    "integer": frozenset({"integer"}),
    "rating": frozenset({"integer"}),
    "boolean": frozenset({"boolean"}),
    "date": frozenset({"date", "text"}),
    "datetime": frozenset({"text"}),
    "single_select": frozenset({"choice", "text"}),
    "multi_select": frozenset({"string_list"}),
}


def ports_for(config: BaseModel | None) -> tuple[Port, ...]:
    """`out`, carrying `fields` typed by the declared columns when there are any."""
    typed = (
        typed_trigger_output(config, TableRecordTriggerOutput, "fields")
        if isinstance(config, TableRecordTriggerConfig)
        else None
    )
    schema = typed if typed is not None else TableRecordTriggerOutput
    return (Port(id="out", label="Out", kind="output", schema=schema),)


async def check_resources(
    db: AsyncSession, ctx: AuthContext, config: BaseModel
) -> list[tuple[str, str]]:
    """The table, readable by the graph's author and current, every filter on one of
    its live columns with an operand that suits the column's type, and every watched
    column one a record stores - a column worked out on read never changes. A
    declared payload field names a live column too, by its label, and claims a
    type the column can hold: a field naming a column the table no longer has,
    or renamed, is a publish problem here, not a missing value at run time."""
    if not isinstance(config, TableRecordTriggerConfig):
        return []
    problems = await check_table(db, ctx, config.table, writes=False)
    if problems:
        return problems
    if not (config.filters or config.table.column_ids or config.payload_fields):
        return problems
    version = await virtual_table_repo.get_schema_version(
        db, table_id=config.table.table_id, version=config.table.schema_version
    )
    live = {
        column.id: column
        for column in (ColumnDef.model_validate(item) for item in version.columns)
        if not column.archived
    }
    for index, column_id in enumerate(config.table.column_ids or []):
        watched = live.get(column_id)
        if watched is None or COLUMN_TYPES[watched.type].computed:
            problems.append(
                (f"table.column_ids.{index}", "A change is watched on a column a record stores")
            )
    for index, condition in enumerate(config.filters):
        column = live.get(condition.column_id)
        if column is None:
            problems.append((f"filters.{index}.column_id", "Not a live column of this table"))
            continue
        try:
            validate_filter(column, condition.op, condition.value)
        except CellProblem as problem:
            problems.append((f"filters.{index}", str(problem)))
    by_label = {column.label: column for column in live.values()}
    for index, field in enumerate(config.payload_fields):
        column = by_label.get(field.name)
        if column is None:
            problems.append(
                (
                    f"payload_fields.{index}.name",
                    f"No live column of this table is called {field.name!r}",
                )
            )
            continue
        allowed = _COLUMN_PAYLOAD_TYPES.get(column.type)
        if allowed is not None and field.type not in allowed:
            problems.append(
                (
                    f"payload_fields.{index}.type",
                    f"This column holds {column.type}, not {field.type}",
                )
            )
    return problems


async def handle(config: BaseModel | None, node_input: BaseModel | None) -> NodeResult:
    """Hand on the record the run was started with."""
    record = run_input_as(TableRecordTriggerOutput)
    if isinstance(record, Failed):
        return record
    return Completed[TableRecordTriggerOutput](output=record)
