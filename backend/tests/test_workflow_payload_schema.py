"""Declared trigger payload schemas: contracts, ports and admission.

Tier 1 is a flat list of `PayloadField`s, Tier 2 a Draft 2020-12 JSON Schema.
A delivery that does not fit is refused with `WORKFLOW_RUN_INPUT_INVALID`
and starts no run; a declared field is typed at publish through the
trigger's `ports_for`, while an undeclared payload stays dynamic.
"""

import json
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.permissions import AuthContext, OrgRoleName
from app.services.workflow_execution.exceptions import WorkflowRunInputInvalidError
from app.services.workflow_exposure import WorkflowExposureService
from app.workflows.contracts.payload_schema import (
    PayloadField,
    PayloadSchemaConfig,
    effective_payload_schema,
    json_schema_model,
    payload_problems,
    validate_payload,
)
from app.workflows.graph.model import NodeInstance, NodePosition, WorkflowGraph
from app.workflows.nodes._triggers import (
    ScheduleTriggerConfig,
    ScheduleTriggerOutput,
    TableRecordTriggerConfig,
    TableRecordTriggerOutput,
    WebhookTriggerOutput,
)
from app.workflows.nodes.trigger_github._handler import GithubTriggerConfig
from app.workflows.nodes.trigger_github._handler import ports_for as github_ports
from app.workflows.nodes.trigger_schedule._handler import ports_for as schedule_ports
from app.workflows.nodes.trigger_table_record._handler import ports_for as table_ports
from app.workflows.nodes.trigger_webhook._handler import WebhookTriggerConfig
from app.workflows.nodes.trigger_webhook._handler import ports_for as webhook_ports

pytestmark = pytest.mark.anyio


def _owner_ctx() -> AuthContext:
    return AuthContext(user_id=uuid4(), organization_id=uuid4(), role=OrgRoleName.OWNER.value)


# The declaration itself


def test_a_payload_field_is_named_by_its_key_not_a_form_name():
    field = PayloadField.model_validate({"name": "orderId", "type": "text"})
    assert field.name == "orderId"
    assert PayloadField.model_validate({"name": "First name", "type": "text"}).name == "First name"
    for name in ("", "a.b", " leading", "trailing ", "with\nnewline"):
        with pytest.raises(ValidationError):
            PayloadField.model_validate({"name": name, "type": "text"})
    with pytest.raises(ValidationError):
        PayloadField.model_validate({"name": "copy", "type": "text"})


def test_a_payload_choice_needs_options_and_nothing_else_has_them():
    assert PayloadField.model_validate(
        {"name": "priority", "type": "choice", "options": ["high", "low"]}
    ).options == ["high", "low"]
    for fields in (
        {"name": "priority", "type": "choice", "options": []},
        {"name": "priority", "type": "choice", "options": ["high", "high"]},
        {"name": "title", "type": "text", "options": ["x"]},
    ):
        with pytest.raises(ValidationError):
            PayloadField.model_validate(fields)


def test_a_trigger_declares_either_fields_or_a_schema_not_both():
    assert (
        PayloadSchemaConfig(payload_fields=[PayloadField(name="title", type="text")]).payload_schema
        is None
    )
    assert PayloadSchemaConfig(payload_schema={"type": "object"}).payload_fields == []
    with pytest.raises(ValidationError):
        PayloadSchemaConfig(
            payload_fields=[PayloadField(name="title", type="text")],
            payload_schema={"type": "object"},
        )
    with pytest.raises(ValidationError):
        PayloadSchemaConfig(
            payload_fields=[
                PayloadField(name="title", type="text"),
                PayloadField(name="title", type="number"),
            ]
        )
    with pytest.raises(ValidationError):
        PayloadSchemaConfig(payload_schema={"type": "string"})
    with pytest.raises(ValidationError):
        PayloadSchemaConfig(payload_schema={"$ref": "https://example.com/schema.json"})


def test_the_four_triggers_carry_the_declaration_in_their_config():
    table = {"table_id": str(uuid4()), "schema_version": 1}
    assert (
        WebhookTriggerConfig(payload_fields=[PayloadField(name="title", type="text")])
        .payload_fields[0]
        .name
        == "title"
    )
    assert GithubTriggerConfig(
        repository="acme/api", payload_schema={"type": "object"}
    ).payload_schema == {"type": "object"}
    assert (
        ScheduleTriggerConfig(payload_fields=[PayloadField(name="region", type="text")])
        .payload_fields[0]
        .name
        == "region"
    )
    assert (
        TableRecordTriggerConfig(
            table=table, payload_fields=[PayloadField(name="Email", type="text")]
        )
        .payload_fields[0]
        .name
        == "Email"
    )


# Tier 1 admission validation


def _webhook_config() -> WebhookTriggerConfig:
    return WebhookTriggerConfig(
        payload_fields=[
            PayloadField(name="title", type="text"),
            PayloadField(name="count", type="integer", required=False),
            PayloadField(name="priority", type="choice", options=["high", "low"]),
            PayloadField(name="due", type="date", required=False),
        ]
    )


def test_tier_1_accepts_a_payload_that_fits():
    assert payload_problems(_webhook_config(), {"title": "hi", "priority": "high"}) == []


def test_tier_1_for_a_github_payload_checks_only_the_declared_scalar_keys():
    # A GitHub delivery's top-level keys include `repository` and `sender`
    # objects, which a flat field list cannot declare: Tier 1 fits a payload
    # whose top-level keys are all declared scalars, and a nested delivery
    # needs Tier 2. The unit check below is on the declared keys alone.
    config = GithubTriggerConfig(
        repository="acme/api",
        payload_fields=[
            PayloadField(name="action", type="text"),
            PayloadField(name="title", type="text"),
        ],
    )
    assert payload_problems(config, {"action": "opened", "title": "Fix it"}) == []
    assert payload_problems(config, {"action": "opened"}) != []
    assert payload_problems(config, {"action": "opened", "title": 7}) != []
    assert payload_problems(config, {"action": "opened", "title": "Fix it", "extra": 1}) != []


def test_tier_1_refuses_a_missing_required_field():
    problems = payload_problems(_webhook_config(), {"priority": "high"})
    assert [(problem["field"], problem["type"]) for problem in problems] == [("title", "missing")]
    assert all("hi" not in problem["message"] for problem in problems)


def test_tier_1_refuses_a_wrong_type_and_an_undeclared_field():
    problems = payload_problems(
        _webhook_config(), {"title": "hi", "count": "three", "priority": "high"}
    )
    assert [(problem["field"], problem["type"]) for problem in problems] == [("count", "int_type")]
    problems = payload_problems(
        _webhook_config(), {"title": "hi", "priority": "high", "surprise": 1}
    )
    assert [(problem["field"], problem["type"]) for problem in problems] == [
        ("surprise", "extra_forbidden")
    ]


def test_tier_1_checks_dates_choices_and_optional_fields():
    config = _webhook_config()
    assert payload_problems(config, {"title": "hi", "priority": "urgent"}) != []
    assert payload_problems(config, {"title": "hi", "priority": "high", "due": "2026-13-01"}) != []
    assert (
        payload_problems(
            config, {"title": "hi", "priority": "high", "due": "2026-11-25", "count": 3}
        )
        == []
    )


def test_no_declaration_means_no_problems():
    assert payload_problems(WebhookTriggerConfig(), {"anything": "goes"}) == []
    assert payload_problems(_webhook_config(), "not an object") != []


# Tier 2 admission validation


_NESTED_SCHEMA = {
    "type": "object",
    "properties": {
        "pull_request": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "number": {"type": "integer"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
        "labels": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["pull_request"],
    "additionalProperties": False,
}


def test_tier_2_accepts_a_nested_payload_that_fits():
    assert (
        validate_payload(
            _NESTED_SCHEMA,
            {"pull_request": {"title": "Fix it", "number": 12}, "labels": ["bug"]},
        )
        == []
    )


def test_tier_2_names_the_nested_field_that_does_not_fit():
    problems = validate_payload(_NESTED_SCHEMA, {"pull_request": {"number": "twelve"}})
    assert {(problem["field"], problem["type"]) for problem in problems} == {
        ("pull_request.title", "required"),
        ("pull_request.number", "type"),
    }
    problems = validate_payload(
        _NESTED_SCHEMA, {"pull_request": {"title": "Fix it"}, "extra": True}
    )
    assert [(problem["field"], problem["type"]) for problem in problems] == [
        ("extra", "additionalProperties")
    ]


def test_tier_2_follows_a_local_ref_and_rejects_an_unresolved_one():
    schema = {
        "type": "object",
        "properties": {"pr": {"$ref": "#/$defs/pr"}},
        "$defs": {
            "pr": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            }
        },
    }
    assert validate_payload(schema, {"pr": {"title": "Fix it"}}) == []
    assert validate_payload(schema, {"pr": {}}) == [
        {"field": "pr.title", "message": "Field required", "type": "required"}
    ]
    with pytest.raises(ValidationError):
        PayloadSchemaConfig(payload_schema={"$ref": "#/$defs/missing"})


def test_tier_2_diagnostics_never_quote_the_submitted_value():
    problems = validate_payload(
        {"type": "object", "properties": {"secret": {"enum": ["allowed"]}}},
        {"secret": "do-not-quote-this"},
    )
    assert problems and all("do-not-quote-this" not in problem["message"] for problem in problems)


# The portable contract


def test_tier_1_fields_read_back_as_a_closed_json_schema():
    schema = effective_payload_schema(_webhook_config())
    assert schema is not None
    assert schema["required"] == ["title", "priority"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["count"] == {
        "anyOf": [
            {"type": "integer", "x-agenticos-strict-integer": True},
            {"type": "null"},
        ],
        "title": "count",
    }
    assert schema["properties"]["priority"]["enum"] == ["high", "low"]
    assert effective_payload_schema(WebhookTriggerConfig()) is None
    assert (
        effective_payload_schema(WebhookTriggerConfig(payload_schema=_NESTED_SCHEMA))
        == _NESTED_SCHEMA
    )


# Tier 2 schemas as models, for publish-time path typing


def test_a_nested_schema_types_its_paths():
    model = json_schema_model(_NESTED_SCHEMA)
    assert model is not None
    assert set(model.model_fields) == {"pull_request", "labels"}
    nested = model.model_fields["pull_request"].annotation
    assert set(nested.model_fields) == {"title", "number"}
    assert nested.model_fields["title"].annotation is str
    assert nested.model_fields["number"].annotation == int | None


def test_a_closed_schema_refuses_undeclared_keys_and_an_open_one_does_not():
    closed = json_schema_model(_NESTED_SCHEMA)
    assert closed is not None
    assert closed.model_config["extra"] == "forbid"
    open_model = json_schema_model({"type": "object", "properties": {"title": {"type": "string"}}})
    assert open_model is not None
    assert open_model.model_config["extra"] == "allow"


def test_a_recursive_schema_stays_dynamic_instead_of_looping():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {"child": {"$ref": "#"}},
            "additionalProperties": False,
        }
    )
    assert model is not None
    assert model.model_fields["child"].annotation == Any | None


def test_an_untypable_schema_is_left_dynamic():
    model = json_schema_model(
        {"type": "object", "properties": {"any": {"allOf": [{"type": "string"}]}}}
    )
    assert model is not None
    assert model.model_fields["any"].annotation == Any | None


def test_an_enum_becomes_a_literal_and_an_array_its_items():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "priority": {"type": "string", "enum": ["high", "low"]},
                "tags": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["priority", "tags"],
        }
    )
    assert model is not None
    assert model.model_fields["priority"].annotation == Literal["high", "low"]
    assert model.model_fields["tags"].annotation == list[int]


# The typed port each trigger offers


def test_ports_for_types_the_declared_payload_and_stays_static_without_one():
    config = _webhook_config()
    (port,) = webhook_ports(config)
    assert port.schema is not WebhookTriggerOutput
    assert issubclass(port.schema, WebhookTriggerOutput)
    assert port.schema.model_fields["body"].annotation.model_fields["title"].annotation is str
    assert port.schema.model_fields["delivery_id"].annotation is str
    (static,) = webhook_ports(WebhookTriggerConfig())
    assert static.schema is WebhookTriggerOutput
    (static_none,) = webhook_ports(None)
    assert static_none.schema is WebhookTriggerOutput


def test_ports_for_types_each_trigger_payload_in_place():
    github = GithubTriggerConfig(
        repository="acme/api",
        payload_fields=[PayloadField(name="action", type="text")],
    )
    (port,) = github_ports(github)
    assert port.schema.model_fields["payload"].annotation.model_fields["action"].annotation is str
    assert port.schema.model_fields["event"].annotation is str

    schedule = ScheduleTriggerConfig(payload_fields=[PayloadField(name="region", type="text")])
    (port,) = schedule_ports(schedule)
    assert port.schema.model_fields["input"].annotation.model_fields["region"].annotation is str
    assert "fired_at" in port.schema.model_fields
    assert issubclass(port.schema, ScheduleTriggerOutput)

    table = TableRecordTriggerConfig(
        table={"table_id": str(uuid4()), "schema_version": 1},
        payload_fields=[PayloadField(name="Email", type="text")],
    )
    (port,) = table_ports(table)
    assert port.schema.model_fields["fields"].annotation.model_fields["Email"].annotation is str
    assert port.schema.model_fields["values"].annotation == dict[str, Any]
    assert issubclass(port.schema, TableRecordTriggerOutput)


def test_a_tier_2_schema_types_the_port_too():
    config = WebhookTriggerConfig(payload_schema=_NESTED_SCHEMA)
    (port,) = webhook_ports(config)
    body = port.schema.model_fields["body"].annotation
    assert body.model_fields["pull_request"].annotation.model_fields["title"].annotation is str


def test_the_catalog_carries_the_declaration_shape_in_the_config_schema():
    properties = WebhookTriggerConfig.model_json_schema()["properties"]
    assert {"payload_fields", "payload_schema"} <= set(properties)
    github = GithubTriggerConfig.model_json_schema()["properties"]
    assert {"payload_fields", "payload_schema", "repository"} <= set(github)


# Admission


def _service() -> WorkflowExposureService:
    db = MagicMock(commit=AsyncMock(), info={})
    return WorkflowExposureService(db)


def _exposure(*, adapter: str, node_id: uuid4) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        adapter=adapter,
        is_active=True,
        organization_id=uuid4(),
        workflow_version_id=uuid4(),
        node_instance_id=node_id,
        secret_encrypted="secret",
        secret_key_version=1,
    )


def _graph(node_id: uuid4, definition_id: str) -> WorkflowGraph:
    return WorkflowGraph(
        entry_node_id=node_id,
        nodes=(
            NodeInstance(
                id=node_id,
                definition_id=definition_id,
                definition_version=1,
                config={},
                layout=NodePosition(x=0, y=0),
            ),
        ),
    )


def _webhook_patches(
    service: WorkflowExposureService,
    exposure: SimpleNamespace,
    config: WebhookTriggerConfig,
    *,
    admit: AsyncMock,
) -> list[Any]:
    repo = MagicMock(
        get_by_id=AsyncMock(return_value=exposure),
        lock_delivery=AsyncMock(),
        get_delivery=AsyncMock(return_value=None),
        lock_signed_copy=AsyncMock(),
        get_signed_copy=AsyncMock(return_value=None),
        create_delivery=AsyncMock(),
        update=AsyncMock(),
    )
    return [
        patch("app.services.workflow_exposure.workflow_exposure_repo", repo),
        patch.object(service, "_secret_of", return_value="secret"),
        patch.object(service, "_webhook_config", new=AsyncMock(return_value=config)),
        patch.object(
            service,
            "_fire_context",
            new=AsyncMock(
                return_value=(
                    _owner_ctx(),
                    MagicMock(),
                    SimpleNamespace(
                        id=uuid4(),
                        graph=_graph(exposure.node_instance_id, "trigger.webhook").model_dump(
                            mode="json"
                        ),
                    ),
                )
            ),
        ),
        patch.object(service.runs, "admit_pinned", new=admit),
        patch(
            "app.services.workflow_exposure.trigger_events.verify_signature",
            return_value=True,
        ),
        patch(
            "app.services.workflow_exposure.trigger_events.delivery_id",
            return_value="d1",
        ),
    ]


async def test_a_webhook_delivery_that_does_not_fit_is_refused_and_starts_no_run():
    service = _service()
    exposure = _exposure(adapter="webhook", node_id=uuid4())
    admit = AsyncMock()
    body = json.dumps({"priority": "high"}).encode()
    headers = {"x-delivery-id": "d1", "x-hub-signature-256": "sha256=x"}
    with (
        ExitStack() as stack,
        pytest.raises(WorkflowRunInputInvalidError) as refused,
    ):
        for entered in _webhook_patches(service, exposure, _webhook_config(), admit=admit):
            stack.enter_context(entered)
        await service.receive_webhook(exposure.id, body=body, headers=headers)
    assert refused.value.code == "WORKFLOW_RUN_INPUT_INVALID"
    assert [(item["field"], item["message"]) for item in refused.value.details["fields"]] == [
        ("body.title", "Field required")
    ]
    assert admit.await_count == 0


async def test_a_webhook_delivery_that_fits_is_admitted():
    service = _service()
    exposure = _exposure(adapter="webhook", node_id=uuid4())
    run_id = uuid4()
    admit = AsyncMock(return_value=(SimpleNamespace(id=run_id), uuid4()))
    body = json.dumps({"title": "hi", "priority": "high"}).encode()
    headers = {"x-delivery-id": "d1", "x-hub-signature-256": "sha256=x"}
    decision = SimpleNamespace(allowed=True, retry_after_seconds=0)
    with ExitStack() as stack:
        for entered in _webhook_patches(service, exposure, _webhook_config(), admit=admit):
            stack.enter_context(entered)
        stack.enter_context(
            patch(
                "app.services.workflow_exposure.rate_limit.workflow_webhook_allowed",
                new=AsyncMock(return_value=decision),
            )
        )
        admitted = await service.receive_webhook(exposure.id, body=body, headers=headers)
    assert admitted.run_id == run_id
    assert admitted.duplicate is False
    admitted_input = admit.await_args.kwargs["run_input"]
    assert admitted_input["body"] == {"title": "hi", "priority": "high"}


async def test_a_tier_2_webhook_delivery_is_refused_in_the_isolated_validator():
    service = _service()
    exposure = _exposure(adapter="webhook", node_id=uuid4())
    config = WebhookTriggerConfig(payload_schema=_NESTED_SCHEMA)
    admit = AsyncMock()
    body = json.dumps({"pull_request": {"number": 1}}).encode()
    headers = {"x-delivery-id": "d1", "x-hub-signature-256": "sha256=x"}
    with (
        ExitStack() as stack,
        pytest.raises(WorkflowRunInputInvalidError) as refused,
    ):
        for entered in _webhook_patches(service, exposure, config, admit=admit):
            stack.enter_context(entered)
        await service.receive_webhook(exposure.id, body=body, headers=headers)
    assert refused.value.code == "WORKFLOW_RUN_INPUT_INVALID"
    assert ("body.pull_request.title", "Field required") in [
        (item["field"], item["message"]) for item in refused.value.details["fields"]
    ]
    assert admit.await_count == 0


async def test_a_github_delivery_that_does_not_fit_is_refused_and_starts_no_run():
    service = _service()
    node_id = uuid4()
    exposure = _exposure(adapter="github", node_id=node_id)
    # Tier 2: a real delivery carries `repository` and `sender` objects a
    # flat field list cannot declare, so the nested contract is the
    # practical tier for GitHub.
    config = GithubTriggerConfig(
        repository="acme/api",
        events=("issues",),
        payload_schema={
            "type": "object",
            "properties": {
                "pull_request": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                }
            },
            "required": ["pull_request"],
        },
    )
    repo = MagicMock(
        get_by_id=AsyncMock(return_value=exposure),
        lock_delivery=AsyncMock(),
        get_delivery=AsyncMock(return_value=None),
        lock_signed_copy=AsyncMock(),
        get_signed_copy=AsyncMock(return_value=None),
        create_delivery=AsyncMock(),
        update=AsyncMock(),
    )
    admit = AsyncMock()
    body = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": "acme/api"},
            "sender": {"login": "octo"},
            "pull_request": {"number": 12},
        }
    ).encode()
    headers = {
        "x-github-event": "issues",
        "x-github-delivery": "d1",
        "x-hub-signature-256": "sha256=x",
    }
    with (
        ExitStack() as stack,
        pytest.raises(WorkflowRunInputInvalidError) as refused,
    ):
        for entered in (
            patch("app.services.workflow_exposure.workflow_exposure_repo", repo),
            patch.object(service, "_secret_of", return_value="secret"),
            patch.object(service, "_github_config", new=AsyncMock(return_value=config)),
            patch.object(
                service,
                "_fire_context",
                new=AsyncMock(return_value=(_owner_ctx(), MagicMock(), MagicMock())),
            ),
            patch.object(service.runs, "admit_pinned", new=admit),
            patch(
                "app.services.workflow_exposure.trigger_events.verify_signature",
                return_value=True,
            ),
            patch(
                "app.services.workflow_exposure.trigger_events.delivery_id",
                return_value="d1",
            ),
        ):
            stack.enter_context(entered)
        await service.receive_github(exposure.id, body=body, headers=headers)
    assert refused.value.code == "WORKFLOW_RUN_INPUT_INVALID"
    assert ("payload.pull_request.title", "Field required") in [
        (item["field"], item["message"]) for item in refused.value.details["fields"]
    ]
    assert admit.await_count == 0


# Converter edges: the branches the happy paths never take


def test_a_date_not_written_yyyy_mm_dd_is_refused():
    problems = payload_problems(
        _webhook_config(), {"title": "hi", "priority": "high", "due": "2026-1-5"}
    )
    assert [(problem["field"], problem["type"]) for problem in problems] == [("due", "value_error")]


def test_an_explicit_empty_schema_declaration_is_no_declaration():
    config = PayloadSchemaConfig(payload_schema=None)
    assert config.payload_schema is None
    assert payload_problems(config, {"anything": "goes"}) == []


def test_tier_1_fields_read_back_with_lists_and_descriptions():
    config = WebhookTriggerConfig(
        payload_fields=[
            PayloadField(
                name="tags", type="string_list", required=False, description="Labels to attach"
            ),
        ]
    )
    schema = effective_payload_schema(config)
    assert schema is not None
    assert schema["properties"]["tags"] == {
        "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}],
        "title": "tags",
        "description": "Labels to attach",
    }


def test_payload_problems_checks_a_tier_2_schema_in_process():
    config = WebhookTriggerConfig(payload_schema=_NESTED_SCHEMA)
    assert payload_problems(config, {"pull_request": {"title": "Fix it"}}) == []
    problems = payload_problems(config, {"pull_request": {}})
    assert [(problem["field"], problem["type"]) for problem in problems] == [
        ("pull_request.title", "required")
    ]
    assert payload_problems(config, "not an object") != []


def test_an_enum_too_large_or_not_scalar_stays_dynamic():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "many": {"type": "integer", "enum": list(range(51))},
                "odd": {"type": "string", "enum": [{"nested": "object"}]},
            },
        }
    )
    assert model is not None
    assert model.model_fields["many"].annotation == int | None
    assert model.model_fields["odd"].annotation == str | None


def test_a_deeply_nested_schema_stays_dynamic_at_the_depth_cap():
    node: dict[str, Any] = {"type": "object", "properties": {}}
    schema = node
    for _ in range(40):
        child: dict[str, Any] = {"type": "object", "properties": {}}
        node["properties"]["x"] = child
        node = child
    model = json_schema_model(schema)
    assert model is not None
    current: Any = model
    for _ in range(40):
        annotation = current.model_fields["x"].annotation
        members = [arg for arg in getattr(annotation, "__args__", ()) if arg is not type(None)]
        annotation = members[0] if len(members) == 1 else annotation
        if annotation is Any:
            break
        current = annotation
    else:
        raise AssertionError("no level stayed dynamic")


def test_a_boolean_subschema_stays_dynamic():
    model = json_schema_model(
        {"type": "object", "properties": {"free": {"type": "array", "items": True}}}
    )
    assert model is not None
    assert model.model_fields["free"].annotation == list[Any] | None


def test_a_non_dict_node_converts_to_any():
    # Only reachable through the converter directly: a validated schema never
    # holds a non-dict, non-bool subschema, but the converter must not guess.
    from app.workflows.contracts.payload_schema import _convert

    assert (
        _convert("not a schema", MagicMock(), name="Raw", depth=0, in_progress=frozenset()) is Any
    )


def test_a_dynamic_ref_and_a_ref_with_siblings_stay_dynamic():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "dyn": {"$dynamicRef": "#foo"},
                "shaped": {"$ref": "#/$defs/pr", "type": "object"},
            },
            "$defs": {"pr": {"type": "object", "properties": {"title": {"type": "string"}}}},
        }
    )
    assert model is not None
    assert model.model_fields["dyn"].annotation == Any | None
    assert model.model_fields["shaped"].annotation == Any | None


def test_an_unresolvable_ref_stays_dynamic():
    model = json_schema_model(
        {"type": "object", "properties": {"gone": {"$ref": "#/$defs/missing"}}}
    )
    assert model is not None
    assert model.model_fields["gone"].annotation == Any | None


def test_a_local_ref_types_the_nested_model():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {"pr": {"$ref": "#/$defs/pr"}},
            "$defs": {
                "pr": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                }
            },
        }
    )
    assert model is not None
    annotation = model.model_fields["pr"].annotation
    members = [arg for arg in getattr(annotation, "__args__", ()) if arg is not type(None)]
    nested = members[0] if len(members) == 1 else annotation
    assert nested.model_fields["title"].annotation is str


def test_a_const_becomes_a_literal():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "only": {"const": "fixed"},
                "odd": {"const": {"nested": "object"}, "type": "string"},
            },
        }
    )
    assert model is not None
    assert model.model_fields["only"].annotation == Literal["fixed"] | None
    assert model.model_fields["odd"].annotation == str | None


def test_a_union_schema_types_its_members():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "either": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                "any_branch_dynamic": {
                    "anyOf": [{"type": "string"}, {"allOf": [{"type": "integer"}]}]
                },
                "nothing": {"oneOf": [{"type": "null"}]},
            },
        }
    )
    assert model is not None
    assert model.model_fields["either"].annotation == str | int | None
    assert model.model_fields["any_branch_dynamic"].annotation == Any | None
    assert model.model_fields["nothing"].annotation is type(None)


def test_a_type_list_types_its_members():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "maybe": {"type": ["string", "null"]},
                "both": {"type": ["string", "integer"]},
                "single": {"type": ["string", 5]},
                "untyped": {"type": [5]},
            },
        }
    )
    assert model is not None
    assert model.model_fields["maybe"].annotation == str | None
    assert model.model_fields["both"].annotation == str | int | None
    assert model.model_fields["single"].annotation == str | None
    assert model.model_fields["untyped"].annotation == Any | None


def test_too_many_properties_stay_dynamic():
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {f"f{index}": {"type": "string"} for index in range(201)},
    }
    assert json_schema_model(schema) is None


def test_an_additional_properties_schema_stays_dynamic():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "holder": {"type": "object", "additionalProperties": {"type": "string"}}
            },
        }
    )
    assert model is not None
    assert model.model_fields["holder"].annotation == Any | None


def test_a_reserved_property_name_is_skipped():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "copy": {"type": "string"},
                "title": {"type": "string"},
            },
        }
    )
    assert model is not None
    assert set(model.model_fields) == {"title"}


def test_a_tuple_schema_types_its_prefix_items():
    model = json_schema_model(
        {
            "type": "object",
            "properties": {
                "pair": {
                    "type": "array",
                    "prefixItems": [{"type": "string"}, {"type": "integer"}],
                }
            },
        }
    )
    assert model is not None
    assert model.model_fields["pair"].annotation == tuple[str, int] | None


def test_an_array_without_items_is_untyped():
    model = json_schema_model({"type": "object", "properties": {"values": {"type": "array"}}})
    assert model is not None
    assert model.model_fields["values"].annotation == list[Any] | None


def test_a_non_object_schema_has_no_model():
    assert json_schema_model({"type": "string"}) is None
    assert json_schema_model(True) is None  # type: ignore[arg-type]


def test_a_schema_with_no_typeable_root_has_no_model():
    assert json_schema_model({"$defs": {"a": {"$anchor": "bad!"}}}) is None


def test_an_indexing_failure_has_no_model(monkeypatch):
    monkeypatch.setattr(
        "app.workflows.contracts.payload_schema._schema_resolver",
        MagicMock(side_effect=ValueError("boom")),
    )
    assert json_schema_model({"type": "object"}) is None


def test_an_untypable_tier_2_schema_leaves_the_port_static():
    config = WebhookTriggerConfig(
        payload_schema={
            "anyOf": [
                {"type": "string"},
                {"type": "object", "properties": {"title": {"type": "string"}}},
            ]
        }
    )
    (port,) = webhook_ports(config)
    assert port.schema is WebhookTriggerOutput
