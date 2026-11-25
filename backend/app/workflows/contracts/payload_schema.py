"""A declared shape for an event trigger's payload - webhook body, GitHub
payload, schedule input, table-record fields.

Two tiers, mirroring the trigger input contracts (`core.input`): Tier 1, a flat
list of `PayloadField`s; Tier 2, a portable JSON Schema Draft 2020-12 contract.
One at a time: a field list and a schema cannot be combined, as with the
existing input contracts.

`PayloadField` is deliberately not `InputField`: an input field is a value the
run dialog asks for by a snake_case name, while a payload key is whatever the
sender sent - `orderId`, a GitHub `pull_request`, a table column's `First name`.
The name rule is a JSON key's, not a form field's: any non-empty key without a
dot (a dot would break the `{{Trigger.body.a.b}}` path it is bound by) or
control character. The type, required and options semantics are the same.
"""

import re
from copy import deepcopy
from datetime import date
from types import NoneType
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from app.workflows.contracts.fields import MAX_OPTIONS
from app.workflows.contracts.input_schema import (
    InputSchema,
    check_input_schema,
    schema_input_problems,
)

MAX_PAYLOAD_FIELDS = 50
MAX_PAYLOAD_FIELD_NAME = 128
# A generated model stays small enough to build on every validation: a schema
# with more properties than this stays dynamic for its object, not untyped.
MAX_MODEL_PROPERTIES = 200
MAX_MODEL_DEPTH = 32
MAX_ENUM_OPTIONS = 50

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Draft 2020-12 `format` is an annotation for ordinary validators. Preserve the
# legacy calendar assertion without requiring clients to opt into format checks.
# The sibling in `core_input` carries the same pattern for input fields.
_CALENDAR_DATE_PATTERN = (
    r"^(?:(?!0000)[0-9]{4}-(?:"
    r"(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])|"
    r"(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)|"
    r"02-(?:0[1-9]|1[0-9]|2[0-8]))|"
    r"(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|"
    r"(?:0[48]|[2468][048]|[13579][26])00)-02-29)$"
)

PayloadFieldType = Literal["text", "string_list", "number", "integer", "boolean", "date", "choice"]


class PayloadField(BaseModel):
    """One value a trigger's payload declares, named by its key in the payload."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=MAX_PAYLOAD_FIELD_NAME,
        description="The key in the payload, as the sender sends it or the table labels it",
    )
    label: str | None = Field(default=None, max_length=120, description="Shown in the editor")
    type: PayloadFieldType = "text"
    required: bool = True
    description: str | None = Field(default=None, max_length=500)
    options: list[str] = Field(
        default_factory=list, max_length=MAX_OPTIONS, description="What a choice offers"
    )

    @field_validator("name")
    @classmethod
    def _name_is_a_key(cls, name: str) -> str:
        # A dot would split the binding path; a control character or
        # surrounding whitespace cannot be typed into `{{...}}` reliably.
        if "." in name or name != name.strip() or any(ord(character) < 0x20 for character in name):
            raise ValueError("A payload field is named by one key, with no dot in it")
        if hasattr(BaseModel, name):
            raise ValueError(f"{name!r} is reserved; name the field something else")
        return name

    @model_validator(mode="after")
    def _fits_its_type(self) -> PayloadField:
        if self.type == "choice":
            if not self.options or any(not option.strip() for option in self.options):
                raise ValueError("A choice needs at least one option, and none of them blank")
            if len(set(self.options)) != len(self.options):
                raise ValueError("A choice offers each option once")
        elif self.options:
            raise ValueError("Only a choice has options")
        return self


class PayloadSchemaConfig(BaseModel):
    """The optional declared shape of one trigger's payload, either tier.

    Mixed into a trigger's config model: the fields live on the trigger's own
    config, so the catalog's `config_schema` serialization carries the
    declaration and `ports_for` can type the trigger's output port from it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload_fields: list[PayloadField] = Field(
        default_factory=list,
        max_length=MAX_PAYLOAD_FIELDS,
        description="The payload's declared fields, by key",
    )
    payload_schema: InputSchema | None = Field(
        default=None,
        description="JSON Schema Draft 2020-12 contract for the payload object; "
        "references must be local to this document",
    )

    @field_validator("payload_schema")
    @classmethod
    def _valid_schema(cls, schema: InputSchema | None) -> InputSchema | None:
        if schema is not None:
            check_input_schema(schema)
        return schema

    @model_validator(mode="after")
    def _one_contract(self) -> PayloadSchemaConfig:
        if self.payload_fields and self.payload_schema is not None:
            raise ValueError("Declare either simple fields or payload_schema, not both")
        names = [field.name for field in self.payload_fields]
        if len(set(names)) != len(names):
            raise ValueError("Two payload fields have the same name")
        return self


def _iso_date(value: str) -> str:
    if not _ISO_DATE.match(value):
        raise ValueError("A date is written YYYY-MM-DD")
    date.fromisoformat(value)
    return value


def _value_type(field: PayloadField) -> Any:
    """The type one field's value has in the payload model.

    A date and a choice are strings a later step binds to as strings, checked
    on the way in rather than typed apart. The sibling in `core_input` does
    the same for input fields.
    """
    if field.type == "choice":
        options = frozenset(field.options)

        def _offered(value: str) -> str:
            if value not in options:
                raise ValueError("Not one of the options")
            return value

        return Annotated[str, AfterValidator(_offered)]
    if field.type == "date":
        return Annotated[str, AfterValidator(_iso_date)]
    return {
        "text": str,
        "string_list": list[str],
        "number": float,
        "integer": int,
        "boolean": bool,
    }[field.type]


def payload_model(fields: list[PayloadField]) -> type[BaseModel]:
    """The model a payload must fit: every declared field, and nothing else.

    Strict, so `"3"` is not a whole number and `1` is not `true`: the payload
    is JSON, and a sender sends each value as its own type. Only a typing and
    admission device: no run input is ever validated against it, since the
    trigger's output model is what the handler checks.
    """
    definitions: dict[str, Any] = {
        field.name: (
            _value_type(field) if field.required else _value_type(field) | None,
            Field(... if field.required else None, title=field.label or field.name),
        )
        for field in fields
    }
    return create_model(
        "Payload",
        __config__=ConfigDict(extra="forbid", strict=True, protected_namespaces=()),
        **definitions,
    )


def effective_payload_schema(config: PayloadSchemaConfig) -> dict[str, JsonValue] | None:
    """The portable payload contract: the Tier 2 schema as written, or the
    Tier 1 fields as JSON Schema. `None` when the trigger declares nothing.

    Legacy fields retain optional nulls and a closed object. Their strict
    integer token requirement is marked by `x-agenticos-strict-integer`, since
    ordinary JSON Schema treats integral floating-point numbers as integers too.
    Schema defaults are annotations; admission never inserts or coerces values.
    """
    if config.payload_schema is not None:
        return deepcopy(config.payload_schema)
    if not config.payload_fields:
        return None
    properties: dict[str, JsonValue] = {}
    required: list[JsonValue] = []
    types = {
        "text": "string",
        "string_list": "array",
        "number": "number",
        "integer": "integer",
        "boolean": "boolean",
        "date": "string",
        "choice": "string",
    }
    for field in config.payload_fields:
        value: InputSchema = {"type": types[field.type]}
        if field.type == "string_list":
            value["items"] = {"type": "string"}
        elif field.type == "integer":
            value["x-agenticos-strict-integer"] = True
        elif field.type == "choice":
            value["enum"] = list(field.options)
        elif field.type == "date":
            value["format"] = "date"
            value["pattern"] = _CALENDAR_DATE_PATTERN
            value["maxLength"] = 10
        if not field.required:
            value = {"anyOf": [value, {"type": "null"}]}
        else:
            required.append(field.name)
        value["title"] = field.label or field.name
        if field.description:
            value["description"] = field.description
        properties[field.name] = value
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def validate_payload(schema: InputSchema, value: dict[str, JsonValue]) -> list[dict[str, str]]:
    """What is wrong with `value` against a Tier 2 payload schema, in-process.

    Each entry is `{field, message, type}`, naming field paths and rules,
    never quoting the submitted values. Empty when it fits. This is the
    in-process entry point - publish checks, tests and tooling. Admission of
    an untrusted delivery uses the isolated-process
    `app.services.workflow_execution.input_validation.schema_problems` instead,
    so a hostile schema cannot spend the API loop's time or memory.
    """
    return schema_input_problems(schema, value)


def payload_problems(config: PayloadSchemaConfig, value: dict[str, Any]) -> list[dict[str, str]]:
    """What is wrong with `value` against the trigger's declared payload contract.

    Empty when it fits, or when the trigger declares nothing. Tier 2 is
    checked in-process here; admission calls the isolated-process validator
    for the same schema instead.
    """
    if config.payload_schema is not None:
        return validate_payload(config.payload_schema, value)
    if not config.payload_fields:
        return []
    if not isinstance(value, dict):
        return [{"field": "", "message": "Must be a JSON object", "type": "type"}]
    try:
        payload_model(config.payload_fields).model_validate(value)
    except ValidationError as exc:
        return [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors(include_url=False, include_input=False)
        ]
    return []


# --- Tier 2 schemas as Pydantic models, for publish-time path typing ---


def _schema_resolver(schema: InputSchema):  # type: ignore[no-untyped-def]
    """Index the supplied document once; never install external retrieval."""
    resource = DRAFT202012.create_resource(schema)
    base = resource.id() or ""
    return Registry().with_resource(base, resource).crawl().resolver(base)


def _union(members: list[Any]) -> Any:
    """`A | B | C` for annotations assembled at run time."""
    union = members[0]
    for member in members[1:]:
        union = union | member
    return union


def _literal(values: list[JsonValue]) -> Any:
    """An `enum`/`const` as a `Literal`, when it is small and scalar-only."""
    if not values or len(values) > MAX_ENUM_OPTIONS:
        return None
    if not all(value is None or isinstance(value, (str, int, float, bool)) for value in values):
        return None
    # The values are literal at run time; the checker cannot see that.
    return Literal[tuple(values)]  # type: ignore


def _convert(
    node: Any,
    resolver: Any,
    *,
    name: str,
    depth: int,
    in_progress: frozenset[int],
) -> Any:
    """One JSON Schema node as a type, or `Any` when it cannot be typed.

    Conservative toward `Any`: a path through an untyped part stays dynamic,
    allowed at publish and checked at dispatch, exactly as an undeclared
    payload is today. Nothing here narrows a type the schema does not
    guarantee - an unknown keyword is ignored for typing, never guessed at -
    so a wrong model can only ever refuse a binding the schema would have
    allowed, never admit one it forbids. Admission still enforces the schema
    itself, whatever this returns.
    """
    if depth > MAX_MODEL_DEPTH:
        return Any
    if isinstance(node, bool):
        return Any
    if not isinstance(node, dict):
        return Any
    if "$dynamicRef" in node:
        return Any
    reference = node.get("$ref")
    if isinstance(reference, str):
        shape_keys = {
            key
            for key in node
            if key
            not in {
                "$schema",
                "$id",
                "$anchor",
                "$comment",
                "title",
                "description",
                "default",
                "examples",
                "readOnly",
                "writeOnly",
                "deprecated",
            }
        }
        if shape_keys != {"$ref"}:
            # Siblings that shape the value too: merging them with the target
            # is not attempted; the reference alone would over-promise.
            return Any
        try:
            resolved = resolver.lookup(reference)
        except Unresolvable:
            return Any
        contents = resolved.contents
        if id(contents) in in_progress:
            # A recursive definition has no finite model; the cycle itself
            # stays dynamic.
            return Any
        return _convert(
            contents,
            resolved.resolver,
            name=name,
            depth=depth + 1,
            in_progress=in_progress | {id(contents)},
        )
    enum = node.get("enum")
    if isinstance(enum, list):
        literal = _literal(enum)
        if literal is not None:
            return literal
    const = node.get("const", None)
    if "const" in node:
        literal = _literal([const])
        if literal is not None:
            return literal
    for keyword in ("anyOf", "oneOf"):
        branches = node.get(keyword)
        if isinstance(branches, list) and branches:
            converted = [
                _convert(branch, resolver, name=name, depth=depth + 1, in_progress=in_progress)
                for branch in branches
            ]
            if all(branch is not Any for branch in converted):
                members = [branch for branch in converted if branch is not NoneType]
                has_null = len(members) != len(converted)
                if not members:
                    return NoneType
                union = _union(members)
                return union | None if has_null else union
            return Any
    declared = node.get("type")
    if isinstance(declared, list):
        members = [
            _convert(
                {**node, "type": member},
                resolver,
                name=name,
                depth=depth + 1,
                in_progress=in_progress,
            )
            for member in declared
            if isinstance(member, str)
        ]
        members = [member for member in members if member is not Any]
        if not members:
            return Any
        if len(members) == 1:
            return members[0] | None if "null" in declared else members[0]
        return _union(members)
    if declared == "object" or (declared is None and isinstance(node.get("properties"), dict)):
        return _object_model(node, resolver, name=name, depth=depth, in_progress=in_progress)
    if declared == "array" or (
        declared is None
        and (
            isinstance(node.get("items"), (dict, bool)) or isinstance(node.get("prefixItems"), list)
        )
    ):
        return _array_type(node, resolver, name=name, depth=depth, in_progress=in_progress)
    return (
        {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "null": NoneType,
        }.get(declared, Any)
        if isinstance(declared, str)
        else Any
    )


def _object_model(
    node: dict[str, Any],
    resolver: Any,
    *,
    name: str,
    depth: int,
    in_progress: frozenset[int],
) -> Any:
    """An object schema as a model, or `Any` when it cannot be typed.

    `additionalProperties: false` closes the model, so a binding to a key the
    schema does not name is a publish problem. Anything else leaves the model
    open, so an undeclared key resolves to `Any` - allowed at publish,
    checked at dispatch - because the schema itself allows it. A property
    named like a `BaseModel` attribute cannot be a model field; it is left to
    the open model rather than breaking the whole object.
    """
    properties = node.get("properties") or {}
    if not isinstance(properties, dict) or len(properties) > MAX_MODEL_PROPERTIES:
        return Any
    additional = node.get("additionalProperties", True)
    if isinstance(additional, dict):
        # Any key may hold a typed value the model cannot name per key.
        return Any
    required = node.get("required") or []
    required_names = set(required) if isinstance(required, list) else set()
    definitions: dict[str, Any] = {}
    for index, (key, child) in enumerate(properties.items()):
        if not isinstance(key, str) or hasattr(BaseModel, key):
            continue
        child_name = re.sub(r"[^a-zA-Z0-9_]", "_", f"{name}_{index}")
        converted = _convert(
            child, resolver, name=child_name, depth=depth + 1, in_progress=in_progress
        )
        annotation = converted if key in required_names else converted | None
        definitions[key] = (
            annotation,
            Field(... if key in required_names else None, title=key),
        )
    return create_model(
        name,
        __config__=ConfigDict(
            extra="forbid" if additional is False else "allow",
            protected_namespaces=(),
        ),
        **definitions,
    )


def _array_type(
    node: dict[str, Any],
    resolver: Any,
    *,
    name: str,
    depth: int,
    in_progress: frozenset[int],
) -> Any:
    prefix = node.get("prefixItems")
    if isinstance(prefix, list) and prefix:
        items = tuple(
            _convert(item, resolver, name=name, depth=depth + 1, in_progress=in_progress)
            for item in prefix
        )
        # The prefix items are types at run time; the checker cannot see that.
        return tuple[items]  # type: ignore
    items = node.get("items")
    if isinstance(items, (dict, bool)):
        converted = _convert(items, resolver, name=name, depth=depth + 1, in_progress=in_progress)
        # `converted` is a type at run time; the checker cannot see that.
        return list[converted]  # type: ignore
    return list[Any]


def json_schema_model(schema: InputSchema) -> type[BaseModel] | None:
    """The root object schema as a Pydantic model, or `None` when it cannot be typed.

    `None` is not a failure: the caller keeps the trigger's port dynamic and
    admission still enforces the schema. The schema has already passed
    `check_input_schema` (local references only, no endless cycles), so
    resolution here cannot fetch anything or loop forever.
    """
    if not isinstance(schema, dict):
        return None
    try:
        resolver = _schema_resolver(schema)
        converted = _convert(
            schema,
            resolver,
            name="Payload",
            depth=0,
            # The root is already being converted: a reference back to it is
            # recursive, not a second copy of the same object.
            in_progress=frozenset({id(schema)}),
        )
    except RecursionError, ValueError:
        return None
    if not (isinstance(converted, type) and issubclass(converted, BaseModel)):
        return None
    return converted


def typed_trigger_output(
    config: PayloadSchemaConfig,
    output_model: type[BaseModel],
    payload_attr: str,
) -> type[BaseModel] | None:
    """The trigger's output model with its payload attribute typed by the
    declared contract, or `None` when the trigger declares nothing - or
    nothing its schema can be typed as.

    The model is only a typing device for publish checks (`ports_for`): the
    handler still validates the run's input against the trigger's own output
    model, and the dispatcher resolves bindings against the run's real values.
    """
    if config.payload_schema is not None:
        payload = json_schema_model(config.payload_schema)
        if payload is None:
            return None
    elif config.payload_fields:
        payload = payload_model(config.payload_fields)
    else:
        return None
    # The field name is dynamic (body, payload, input or fields), so the
    # definition cannot be a literal keyword argument as in core_input's.
    return create_model(  # type: ignore
        output_model.__name__,
        __base__=output_model,
        __config__=ConfigDict(frozen=True, extra="forbid"),
        **{payload_attr: (payload, Field(default_factory=dict))},
    )
