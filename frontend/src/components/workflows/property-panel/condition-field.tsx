"use client";

import { useId, useMemo, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  Button,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import {
  UNKNOWN,
  resolveFieldType,
  resolveItemType,
  type ResolvedType,
} from "@/components/workflows/validation";
import {
  CONDITION_OPS,
  type Condition,
  type ConditionFieldKind,
  type ConditionFieldSuggestion,
  type ConditionJoin,
  type ConditionOp,
  type ConditionRow,
  compileCondition,
  opsForKind,
  parseCondition,
  takesNoValue,
  valueOptions,
} from "@/lib/workflows/conditions";
import { effectiveDefinition } from "@/lib/workflows/ports";
import type {
  Binding,
  JsonSchema,
  NodeCatalog,
  NodeDefinition,
  Uuid,
  WorkflowGraph,
} from "@/lib/workflows/types";
import { useWorkflowEditorStore } from "@/stores/workflow-editor-store";

import { outputFieldPaths } from "./bindings";
import { isRecord } from "./schema-model";

/** What a condition reads: each item of Filter's list, or If's and Switch's value. */
export type ConditionRoot = "item" | "value";

const EMPTY_ROW: ConditionRow = { field: "", op: "eq", value: "" };
/** The most field names suggested: enough for a record, not a page of them. */
const MAX_SUGGESTIONS = 40;

/** An optional schema's own schema - `X | None` read as `X` - or the schema unchanged. */
function unwrapSingle(schema: JsonSchema): JsonSchema {
  const members = schema["anyOf"] ?? schema["oneOf"];
  if (!Array.isArray(members)) return schema;
  const nonNull = members.filter(
    (member): member is JsonSchema =>
      typeof member === "object" && member !== null && member["type"] !== "null",
  );
  return nonNull.length === 1 && nonNull[0] !== undefined ? nonNull[0] : schema;
}

/** The kind of a field a declared schema types, with an enum's options. */
function declaredKind(schema: ResolvedType): {
  kind: ConditionFieldKind;
  options?: readonly string[];
} {
  if (schema === UNKNOWN || schema === null) return { kind: "unknown" };
  const single = unwrapSingle(schema);
  const enumValues = single["enum"];
  if (Array.isArray(enumValues) && enumValues.every((value) => typeof value === "string")) {
    return { kind: "enum", options: enumValues as string[] };
  }
  switch (single["type"]) {
    case "integer":
    case "number":
      return { kind: "number" };
    case "boolean":
      return { kind: "boolean" };
    case "string":
      return { kind: "string" };
    default:
      return { kind: "unknown" };
  }
}

/** The declared fields under a bound value: the source step's output schema, past the bound path. */
function valueSuggestions(
  definition: NodeDefinition,
  port: string,
  path: readonly string[],
): ConditionFieldSuggestion[] {
  return outputFieldPaths(definition, port)
    .filter(
      (candidate) =>
        candidate.length > path.length && path.every((part, index) => candidate[index] === part),
    )
    .map((candidate) => ({
      field: candidate.slice(path.length).join("."),
      ...declaredKind(resolveFieldType(definition, port, candidate)),
    }));
}

/** The declared fields of one item of a bound list: the item schema's properties. */
function itemSuggestions(
  definition: NodeDefinition,
  port: string,
  path: readonly string[],
): ConditionFieldSuggestion[] {
  const item = resolveItemType(definition, port, path);
  if (item === UNKNOWN || item === null) return [];
  const properties = unwrapSingle(item)["properties"];
  if (typeof properties !== "object" || properties === null || Array.isArray(properties)) {
    return [];
  }
  return Object.entries(properties).map(([field, schema]) => ({
    field,
    ...declaredKind(schema as JsonSchema),
  }));
}

/**
 * The fields a condition can look at: those of what the step reads - the first
 * item of Filter's list, If's value - as the step before declares them, with
 * their types, and beside them the fields the last run or the test data saw.
 * A declared field comes first, so it beats what a run happened to show for
 * the same name; with nothing declared, only the observed fields are offered.
 */
export function conditionFields(
  root: ConditionRoot,
  nodeId: Uuid,
  graph: WorkflowGraph,
  catalog: NodeCatalog,
  bindings: readonly Binding[],
  stepData: Record<string, { output: Record<string, unknown> | null } | undefined>,
): ConditionFieldSuggestion[] {
  const read = bindings.find(
    (binding) =>
      binding.target_node_id === nodeId &&
      binding.target_field === (root === "item" ? "items" : "value"),
  );
  if (read?.source.kind !== "node_output") return [];
  const { node_id: source, port, field_path: path } = read.source;
  const instance = graph.nodes.find((candidate) => candidate.id === source);
  let seen: unknown = instance?.pinned_output ?? stepData[source]?.output ?? null;
  for (const part of path) seen = isRecord(seen) ? seen[part] : undefined;
  const sample = root === "item" ? (Array.isArray(seen) ? seen[0] : undefined) : seen;
  const observed: string[] = isRecord(sample) ? Object.keys(sample) : [];
  const definition = catalog.items.find(
    (entry) =>
      entry.id === instance?.definition_id && entry.version === instance?.definition_version,
  );
  const declared: ConditionFieldSuggestion[] =
    definition === undefined
      ? []
      : root === "item"
        ? itemSuggestions(
            instance === undefined ? definition : effectiveDefinition(instance, definition),
            port,
            path,
          )
        : valueSuggestions(
            instance === undefined ? definition : effectiveDefinition(instance, definition),
            port,
            path,
          );
  const names = new Set(declared.map((suggestion) => suggestion.field));
  return [
    ...declared,
    ...observed
      .filter((field) => !names.has(field))
      .map((field): ConditionFieldSuggestion => ({ field, kind: "unknown" })),
  ].slice(0, MAX_SUGGESTIONS);
}

/**
 * A condition built from rows - a field, how it compares, and with what - joined
 * by "all" or "any", the way n8n builds one. It is stored as the expression the
 * step evaluates; one this builder did not write, or cannot show, is edited as
 * that expression, and **Write it as an expression** turns a built one into it.
 *
 * Rows are kept here while they are being filled: one without a value yet is not
 * part of the expression, and is not lost from the form for it.
 */
export function ConditionField({
  root,
  label,
  required,
  hint,
  value,
  error,
  disabled,
  fields,
  onChange,
}: {
  root: ConditionRoot;
  label: string;
  required: boolean;
  /** What the expression may say, for the expression box. */
  hint?: string;
  value: string | undefined;
  error?: string;
  disabled?: boolean;
  /** Field names to suggest, with the declared type of each. */
  fields: readonly ConditionFieldSuggestion[];
  onChange: (expression: string | undefined) => void;
}) {
  const t = useTranslations("workflows");
  const id = useId();
  const saved = value ?? "";
  const parsed =
    saved === "" ? { rows: [EMPTY_ROW], join: "and" as const } : parseCondition(saved, root);
  const [built, setBuilt] = useState<Condition | null>(parsed);
  const [seen, setSeen] = useState(saved);
  // Written elsewhere - an undo, another tab - the form shows what was saved.
  if (saved !== seen) {
    setSeen(saved);
    if (built === null || compileCondition(built, root) !== saved) setBuilt(parsed);
  }

  const write = (next: Condition) => {
    setBuilt(next);
    const expression = compileCondition(next, root);
    setSeen(expression);
    onChange(expression === "" ? undefined : expression);
  };
  const change = (index: number, patch: Partial<ConditionRow>) =>
    write({
      ...(built as Condition),
      rows: (built as Condition).rows.map((row, at) => (at === index ? { ...row, ...patch } : row)),
    });

  const heading = (
    <Label htmlFor={built === null ? `${id}-expression` : undefined}>
      {label}
      {required && <span className="text-muted-foreground"> *</span>}
    </Label>
  );

  if (built === null) {
    return (
      <div className="space-y-1.5">
        {heading}
        <Input
          id={`${id}-expression`}
          className="font-mono text-base md:text-xs"
          value={saved}
          disabled={disabled}
          onChange={(event) => {
            setSeen(event.target.value);
            onChange(event.target.value === "" ? undefined : event.target.value);
          }}
        />
        {hint !== undefined && <p className="text-muted-foreground text-xs">{hint}</p>}
        {error !== undefined && <p className="text-destructive text-xs">{error}</p>}
        {!disabled && (
          <ToggleLink
            disabled={saved !== "" && parseCondition(saved, root) === null}
            title={t("conditionCannotBuild")}
            onClick={() => setBuilt(saved === "" ? { rows: [EMPTY_ROW], join: "and" } : parsed)}
          >
            {t("conditionBuild")}
          </ToggleLink>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {heading}
      {built.rows.length > 1 && (
        <div className="text-muted-foreground flex items-center gap-2 text-xs">
          {t("conditionMatch")}
          <Select
            value={built.join}
            disabled={disabled}
            onValueChange={(join) => write({ ...built, join: join as ConditionJoin })}
          >
            <SelectTrigger className="h-7 w-auto text-xs" aria-label={t("conditionJoin")}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="and">{t("conditionJoinAnd")}</SelectItem>
              <SelectItem value="or">{t("conditionJoinOr")}</SelectItem>
            </SelectContent>
          </Select>
          {t("conditionOfThese")}
        </div>
      )}
      <datalist id={`${id}-fields`}>
        {fields.map((field) => (
          <option key={field.field} value={field.field} />
        ))}
      </datalist>
      <ul className="space-y-2">
        {built.rows.map((row, index) => {
          const suggestion = fields.find((candidate) => candidate.field === row.field);
          const ops = suggestion === undefined ? CONDITION_OPS : opsForKind(suggestion.kind);
          const options = suggestion === undefined ? null : valueOptions(suggestion);
          return (
            <li
              key={index}
              className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto] gap-2"
            >
              <Input
                list={`${id}-fields`}
                aria-label={t("conditionField", { index: index + 1 })}
                placeholder={t(root === "item" ? "conditionFieldItem" : "conditionFieldValue")}
                value={row.field}
                disabled={disabled}
                onChange={(event) => {
                  const field = event.target.value;
                  const next = fields.find((candidate) => candidate.field === field);
                  const offered = next === undefined ? CONDITION_OPS : opsForKind(next.kind);
                  change(index, offered.includes(row.op) ? { field } : { field, op: "eq" });
                }}
              />
              <Select
                value={row.op}
                disabled={disabled}
                onValueChange={(op) => change(index, { op: op as ConditionOp })}
              >
                <SelectTrigger
                  className="w-auto min-w-36"
                  aria-label={t("conditionOp", { index: index + 1 })}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ops.map((op) => (
                    <SelectItem key={op} value={op}>
                      {t(`conditionOps.${op}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {takesNoValue(row.op) ? (
                <span />
              ) : options !== null ? (
                <Select
                  value={row.value === "" ? undefined : row.value}
                  disabled={disabled}
                  onValueChange={(value) => change(index, { value })}
                >
                  <SelectTrigger
                    className="w-auto min-w-36"
                    aria-label={t("conditionValue", { index: index + 1 })}
                  >
                    <SelectValue placeholder={t("conditionValuePlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {[
                      ...options,
                      // What was saved, when the options no longer offer it.
                      ...(row.value !== "" && !options.includes(row.value) ? [row.value] : []),
                    ].map((option) => (
                      <SelectItem key={option} value={option}>
                        {option}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <Input
                  aria-label={t("conditionValue", { index: index + 1 })}
                  placeholder={t("conditionValuePlaceholder")}
                  value={row.value}
                  disabled={disabled}
                  onChange={(event) => change(index, { value: event.target.value })}
                />
              )}
              <Button
                size="icon"
                variant="ghost"
                aria-label={t("conditionRemove", { index: index + 1 })}
                disabled={disabled || built.rows.length === 1}
                onClick={() =>
                  write({ ...built, rows: built.rows.filter((_, at) => at !== index) })
                }
              >
                <Trash2 className="size-4" />
              </Button>
            </li>
          );
        })}
      </ul>
      {error !== undefined && <p className="text-destructive text-xs">{error}</p>}
      {!disabled && (
        <div className="flex items-center gap-3">
          <Button
            size="sm"
            variant="outline"
            onClick={() => write({ ...built, rows: [...built.rows, EMPTY_ROW] })}
          >
            <Plus className="size-4" />
            {t("conditionAdd")}
          </Button>
          <ToggleLink onClick={() => setBuilt(null)}>{t("conditionWrite")}</ToggleLink>
        </div>
      )}
    </div>
  );
}

function ToggleLink({
  onClick,
  disabled,
  title,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      title={disabled ? title : undefined}
      onClick={onClick}
      className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline disabled:no-underline disabled:opacity-50"
    >
      {children}
    </button>
  );
}

/** `ConditionField`'s suggestions, read from the store's run data. */
export function useConditionFields(
  root: ConditionRoot,
  nodeId: Uuid,
  graph: WorkflowGraph,
  catalog: NodeCatalog,
  bindings: readonly Binding[],
): ConditionFieldSuggestion[] {
  const stepData = useWorkflowEditorStore((state) => state.stepData);
  return useMemo(
    () => conditionFields(root, nodeId, graph, catalog, bindings, stepData),
    [root, nodeId, graph, catalog, bindings, stepData],
  );
}
