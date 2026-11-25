"""What each trigger node hands its graph, and the one handler they share.

A workflow starts from exactly one trigger: the node naming the way in - by hand
or the API (`core.input`), a chat message, a signed webhook delivery, a
schedule's tick, a new table record. The trigger's configuration lives in the
graph, and publishing a version is what switches it on
(`app.services.workflow_triggers`). The surface that admits the run stores what
it received as the run's input, already in this node's output shape, so the
handler only checks it and hands it on (`run_input_as`).
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.cron import cron_has_next
from app.db.models.workflow_exposure import MIN_INTERVAL_SECONDS
from app.schemas.agent_trigger import MAX_INTERVAL_SECONDS
from app.schemas.virtual_table import RecordFilter
from app.schemas.virtual_table_trigger import MAX_FILTERS
from app.services.workflow_execution import context
from app.workflows.contracts.io import FileRef, TableIORef
from app.workflows.contracts.payload_schema import PayloadSchemaConfig
from app.workflows.contracts.results import Failed, WorkflowError


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ChatTriggerOutput(_Frozen):
    """The message a member sent in the chat, and where the answer goes."""

    prompt: str
    conversation_id: UUID
    user_id: UUID | None = None


class WebhookTriggerOutput(_Frozen):
    """One signed delivery: its JSON body and the id the sender gave it."""

    body: dict[str, Any] = Field(default_factory=dict)
    delivery_id: str


class ScheduleTriggerConfig(PayloadSchemaConfig):
    """How often the workflow runs, and what every run starts with.

    `payload_fields` / `payload_schema` declare the shape of `input`: the
    run is refused at publish when the configured `input` does not fit, and
    again at tick time when the stored input was edited since.
    """

    schedule_kind: Literal["interval", "cron"] = "interval"
    interval_seconds: int | None = Field(
        default=3600,
        ge=MIN_INTERVAL_SECONDS,
        le=MAX_INTERVAL_SECONDS,
        description="Seconds between runs, at least a minute",
    )
    cron_expression: str | None = Field(
        default=None,
        max_length=255,
        description="Five fields, in the workflow's timezone (UTC unless its settings "
        "name another), such as 0 9 * * 1-5",
    )
    input: dict[str, Any] = Field(
        default_factory=dict, description="What every run starts with, as JSON"
    )

    @model_validator(mode="after")
    def _cadence(self) -> ScheduleTriggerConfig:
        if self.schedule_kind == "interval" and self.interval_seconds is None:
            raise ValueError("An interval schedule needs interval_seconds")
        if self.schedule_kind == "cron" and (
            self.cron_expression is None or not cron_has_next(self.cron_expression)
        ):
            raise ValueError("A cron schedule needs a crontab expression that ever fires")
        return self


class ScheduleTriggerOutputV1(_Frozen):
    """The tick as version 1 handed it on: when it fired, and the input.

    Kept so a published graph pinned to `trigger.schedule` version 1 still
    resolves; version 2 adds the tick's local fields below. New graphs take
    version 2.
    """

    fired_at: datetime
    input: dict[str, Any] = Field(default_factory=dict)


# `strftime("%A")` follows the process locale; the weekday a schedule hands on
# is English by contract, so the names are fixed here.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def schedule_local_fields(fired_at: datetime, timezone: str) -> dict[str, str]:
    """A schedule tick's weekday, date and time in `timezone`, from `fired_at`.

    A moment with no timezone of its own is read as UTC. The date is ISO
    `YYYY-MM-DD` and the time `HH:MM`, both in the timezone asked for.
    """
    moment = fired_at if fired_at.tzinfo is not None else fired_at.replace(tzinfo=UTC)
    local = moment.astimezone(ZoneInfo(timezone))
    return {
        "weekday": _WEEKDAYS[local.weekday()],
        "local_date": local.strftime("%Y-%m-%d"),
        "local_time": local.strftime("%H:%M"),
    }


class ScheduleTriggerOutput(_Frozen):
    """When the tick fired, the input the schedule starts every run with, and
    the tick's weekday, date and time in the workflow's timezone.

    The three local fields are derived from `fired_at` at tick time, never
    author-supplied; `fired_at` stays the source of truth. A run input that
    predates them - a test run, or one stored before version 2 - reads them
    in UTC instead.
    """

    fired_at: datetime
    input: dict[str, Any] = Field(default_factory=dict)
    weekday: str = Field(
        default="",
        description="The day name in the workflow's timezone, such as Monday",
    )
    local_date: str = Field(
        default="", description="The date in the workflow's timezone, YYYY-MM-DD"
    )
    local_time: str = Field(default="", description="The time in the workflow's timezone, HH:MM")

    @model_validator(mode="after")
    def _local_fields(self) -> ScheduleTriggerOutput:
        if self.weekday and self.local_date and self.local_time:
            return self
        derived = schedule_local_fields(self.fired_at, "UTC")
        return self.model_copy(
            update={field: value for field, value in derived.items() if not getattr(self, field)}
        )


RecordEvent = Literal["created", "updated", "deleted"]


class TableRecordTriggerConfig(PayloadSchemaConfig):
    """Which table, which of its records' events, and which records, start the workflow.

    `payload_fields` declares the record's `fields` - the values by column
    label - by hand: the table's live columns are what the declaration is
    checked against at publish, so a field naming a column the table no
    longer has, or of a type the column cannot hold, is a publish problem.
    The columns are not derived into the declaration automatically: the
    catalog and the graph checker are per-definition, while a table's columns
    belong to one table, and deriving them would need a live schema read in
    the (synchronous) port resolution. `values`, keyed by column id rather
    than label, stays untyped.
    """

    table: TableIORef = Field(
        json_schema_extra={"x-resource": "table", "x-columns": "stored"},
        description="The table, and the columns whose change starts it - any column's "
        "when none are chosen.",
    )
    events: tuple[RecordEvent, ...] = Field(
        default=("created",),
        min_length=1,
        max_length=3,
        title="Starts when a record is",
        description="Added, changed or deleted. A change starts it only when one of the "
        "table's chosen columns changed, when columns are chosen.",
        json_schema_extra={
            "x-enum-labels": {"created": "Added", "updated": "Changed", "deleted": "Deleted"}
        },
    )
    filters: list[RecordFilter] = Field(
        default_factory=list,
        max_length=MAX_FILTERS,
        description="Every one must hold on the record - as it was added or changed to, "
        "or as it was before it was deleted",
    )

    @field_validator("events")
    @classmethod
    def _each_once(cls, events: tuple[RecordEvent, ...]) -> tuple[RecordEvent, ...]:
        if len(set(events)) != len(events):
            raise ValueError("Each event is named once")
        return events


class TableRecordTriggerOutput(_Frozen):
    """The record the event is about: its values by column id and by label, who did it,
    and - for a change or a deletion - what it held before."""

    table_id: UUID
    record_id: UUID
    event: RecordEvent = "created"
    values: dict[str, Any] = Field(default_factory=dict)
    fields: dict[str, Any] = Field(default_factory=dict)
    before: dict[str, Any] | None = Field(
        default=None, description="A change's or a deletion's values before it, by column id"
    )
    changed: list[str] = Field(
        default_factory=list, description="A change's columns that changed, by label"
    )
    author_id: UUID | None = None


class ChannelSender(_Frozen):
    """Who sent a channel message: as the platform knows them, and the member they
    linked their account to, when they did."""

    platform_user_id: str
    username: str | None = None
    display_name: str | None = None
    user_id: UUID | None = Field(
        default=None, description="The member this sender linked, if they linked one"
    )


class ChannelMessageTriggerOutput(_Frozen):
    """One message to the bot: what it said, who said it, and where to answer it.

    `channel_id` and `thread_id` are what a Send a message step of the same
    platform takes, so a reply goes back into the thread the message came from.
    """

    platform: str
    bot_id: UUID
    channel_id: str
    thread_id: str = Field(default="", description="The thread, or empty for the chat itself")
    chat_type: Literal["private", "group"]
    message_id: str | None = None
    text: str
    sender: ChannelSender


class GithubTriggerOutput(_Frozen):
    """One GitHub delivery: which event and action, where, by whom, and all of it."""

    event: str = Field(description="GitHub's event name, such as issues or pull_request")
    action: str | None = Field(default=None, description="What happened, such as opened")
    repository: str = Field(description="The repository, as owner/name")
    sender: str | None = Field(default=None, description="The login of whoever did it")
    delivery_id: str
    payload: dict[str, Any] = Field(
        default_factory=dict, description="The delivery's whole JSON body, as GitHub sent it"
    )


AttachmentSkip = Literal[
    "too_many", "too_large", "type_not_allowed", "quota", "unreadable", "not_fetched"
]


class MailAttachment(_Frozen):
    """One attachment the message listed, and the stored file when the trigger staged it."""

    attachment_id: str = Field(
        max_length=4096, description="The mailbox's own id for the attachment"
    )
    name: str = Field(default="", max_length=1024)
    content_type: str = Field(
        default="", max_length=255, description="The type the sender declared"
    )
    byte_size: int | None = Field(default=None, ge=0, description="The size the mailbox declared")
    stored: bool = Field(
        default=False, description="Whether the trigger stored it as a file of this run"
    )
    file: FileRef | None = Field(default=None, description="The stored file, when stored")
    reason: AttachmentSkip | None = Field(default=None, description="Why it was not stored")


class EmailTriggerOutput(_Frozen):
    """One message that arrived in the connected mailbox."""

    message_id: str = Field(description="The mailbox's own id for the message")
    sender: str = Field(default="", description="The From header, name and address")
    to: str = Field(default="", description="The To header")
    subject: str = ""
    text: str = Field(default="", description="The plain-text body, as far as it was read")
    snippet: str = Field(default="", description="The mailbox's short preview of it")
    labels: list[str] = Field(default_factory=list, description="The mailbox's labels on it")
    thread_id: str = Field(default="", description="The mailbox's own conversation key")
    rfc_message_id: str = Field(
        default="", description="The Message-ID header, with angle brackets"
    )
    in_reply_to: str = Field(
        default="", description="The message this one answers, with angle brackets"
    )
    references: list[str] = Field(
        default_factory=list, description="The thread's earlier Message-IDs, oldest first"
    )
    received_at: datetime | None = Field(default=None, description="When the mailbox received it")
    html: str = Field(default="", description="The HTML body, as far as it was read")
    mailbox: str = Field(
        default="", description="The shared mailbox it was read from; empty for the account's own"
    )
    attachments: list[MailAttachment] = Field(default_factory=list)


class FailedRunError(_Frozen):
    """What the failed run ended with: the typed error its failing step recorded."""

    code: str
    message: str


class WorkflowFailedTriggerOutput(_Frozen):
    """The run that failed: which workflow, which step, and with what error."""

    run_id: UUID
    workflow_id: UUID
    workflow_name: str
    step_id: UUID | None = None
    step_name: str | None = None
    error: FailedRunError


def run_input_as[Output: BaseModel](output: type[Output]) -> Output | Failed:
    """The run's input as a trigger hands it on, or the failure when it does not fit.

    Only a test run can fail here: every live surface stores this shape.
    """
    try:
        return output.model_validate(context.current().run_input)
    except ValidationError:
        return Failed(
            error=WorkflowError(
                code="TRIGGER_INPUT_INVALID",
                message="The run's input does not have the shape this trigger hands on",
            )
        )
