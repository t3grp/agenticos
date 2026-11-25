"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import {
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { CodeArea } from "@/components/ui/code-area";
import { inputSchemaOf } from "@/lib/workflows/input-fields";
import { payloadAttrOf } from "@/lib/workflows/triggers";
import type { JsonSchema, NodeInstance, Uuid } from "@/lib/workflows/types";

import { InputFieldsForm } from "./input-fields-form";

interface InputContractFormProps {
  node: NodeInstance;
  disabled?: boolean;
  updateNodeConfig: (nodeId: Uuid, config: Record<string, unknown>) => void;
  /** The config keys the contract lives under - `fields` / `input_schema`, or a payload's. */
  fieldsKey?: string;
  schemaKey?: string;
  /** The binding path the field rows show - `payload`, or the payload attribute. */
  pathPrefix?: string;
  /** A payload's contract: named by the sender's key, and checked at admission. */
  payload?: boolean;
}

const EMPTY_SCHEMA = { $schema: "https://json-schema.org/draft/2020-12/schema", type: "object" };

function contractKey(
  config: Record<string, unknown>,
  fieldsKey: string,
  schemaKey: string,
): string {
  return JSON.stringify([config[fieldsKey] ?? [], config[schemaKey] ?? null]);
}

/** Keep each format's saved edits while this trigger is open; only one is persisted. */
function useContractDrafts({
  node,
  updateNodeConfig,
  fieldsKey = "fields",
  schemaKey = "input_schema",
}: InputContractFormProps) {
  const fromNode = () => ({
    nodeId: node.id,
    current: contractKey(node.config, fieldsKey, schemaKey),
    fields: node.config[fieldsKey] ?? [],
    schema: inputSchemaOf(node.config, schemaKey) ?? EMPTY_SCHEMA,
  });
  const [drafts, setDrafts] = useState(fromNode);
  if (
    drafts.nodeId !== node.id ||
    drafts.current !== contractKey(node.config, fieldsKey, schemaKey)
  ) {
    // An external undo or restore defines a new starting point for both formats.
    setDrafts(fromNode());
  }
  const write = (id: Uuid, config: Record<string, unknown>) => {
    const schema = inputSchemaOf(config, schemaKey);
    setDrafts({
      nodeId: id,
      current: contractKey(config, fieldsKey, schemaKey),
      fields: schema === null ? (config[fieldsKey] ?? []) : drafts.fields,
      schema: schema ?? drafts.schema,
    });
    updateNodeConfig(id, config);
  };
  return { ...drafts, write };
}

/** A portable input contract, or the existing simple-field editor. Forms keep their fields. */
export function InputContractForm(props: InputContractFormProps) {
  const {
    node,
    disabled,
    fieldsKey = "fields",
    schemaKey = "input_schema",
    payload = false,
  } = props;
  const t = useTranslations("workflows.inputFields");
  const schema = inputSchemaOf(node.config, schemaKey);
  const drafts = useContractDrafts(props);
  if (node.definition_id === "trigger.form") return <InputFieldsForm {...props} />;
  return (
    <div className="space-y-3">
      <div className="space-y-1.5" data-tour="workflow-input-contract">
        <Label htmlFor="input-contract-format">
          {t(payload ? "payloadContractFormat" : "contractFormat")}
        </Label>
        <Select
          value={schema === null ? "fields" : "schema"}
          disabled={disabled}
          onValueChange={(value) => {
            if (value === "schema")
              drafts.write(node.id, {
                ...node.config,
                [fieldsKey]: [],
                [schemaKey]: drafts.schema,
              });
            else
              drafts.write(node.id, {
                ...node.config,
                [fieldsKey]: drafts.fields,
                [schemaKey]: null,
              });
          }}
        >
          <SelectTrigger id="input-contract-format">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="fields">{t("simpleFields")}</SelectItem>
            <SelectItem value="schema">{t("jsonSchema")}</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-muted-foreground text-xs">
          {t(payload ? "payloadFormatHint" : "formatHint")}
        </p>
      </div>
      {schema === null ? (
        <InputFieldsForm {...props} updateNodeConfig={drafts.write} />
      ) : (
        <InputSchemaEditor key={node.id} {...props} updateNodeConfig={drafts.write} />
      )}
    </div>
  );
}

/**
 * An event trigger's declared payload: the same two-tier contract as a
 * trigger's input fields, under `payload_fields` / `payload_schema`, naming
 * the output attribute the declaration types. Nothing on a trigger that
 * declares no payload.
 */
export function PayloadContractForm({
  node,
  disabled,
  updateNodeConfig,
}: {
  node: NodeInstance;
  disabled?: boolean;
  updateNodeConfig: (nodeId: Uuid, config: Record<string, unknown>) => void;
}) {
  const attr = payloadAttrOf(node.definition_id);
  if (attr === undefined) return null;
  return (
    <InputContractForm
      node={node}
      disabled={disabled}
      updateNodeConfig={updateNodeConfig}
      fieldsKey="payload_fields"
      schemaKey="payload_schema"
      pathPrefix={attr}
      payload
    />
  );
}

/** A schema being typed must parse as an object before it can replace the saved contract. */
function parseSchema(text: string, schemaKey: string): JsonSchema | null {
  try {
    return inputSchemaOf({ [schemaKey]: JSON.parse(text) }, schemaKey);
  } catch {
    return null;
  }
}

function InputSchemaEditor({
  node,
  disabled,
  updateNodeConfig,
  fieldsKey = "fields",
  schemaKey = "input_schema",
  payload = false,
}: InputContractFormProps) {
  const t = useTranslations("workflows.inputFields");
  const schema = node.config[schemaKey];
  const saved = JSON.stringify(schema);
  const [draft, setDraft] = useState(() => ({ saved, text: JSON.stringify(schema, null, 2) }));
  const parsed = parseSchema(draft.text, schemaKey);
  if (draft.saved !== saved) {
    // Undo and version restores replace the editor's contents; an own save keeps
    // the author's spacing and caret because it represents the same schema.
    setDraft({
      saved,
      text: JSON.stringify(parsed) === saved ? draft.text : JSON.stringify(schema, null, 2),
    });
  }
  return (
    <div className="space-y-1.5">
      <Label htmlFor="trigger-input-schema">{t("jsonSchema")}</Label>
      <p className="text-muted-foreground text-xs">
        {t(payload ? "payloadSchemaHint" : "schemaHint")}
      </p>
      <CodeArea
        id="trigger-input-schema"
        name="input-schema.json"
        aria-label={t("jsonSchema")}
        value={draft.text}
        disabled={disabled}
        invalid={parsed === null}
        className="min-h-48"
        onChange={(next) => {
          setDraft({ saved, text: next });
          const value = parseSchema(next, schemaKey);
          if (value !== null)
            updateNodeConfig(node.id, { ...node.config, [fieldsKey]: [], [schemaKey]: value });
        }}
      />
      {parsed === null && <p className="text-destructive text-xs">{t("schemaInvalid")}</p>}
    </div>
  );
}
