"use client";

import { SchemaForm } from "@/components/agents/schema-form";
import { Checkbox, Input, Label, Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui";
import { useAgents } from "@/hooks";
import type { JsonSchema } from "@/types/agents";
import type { SecretKind, SecretKindInfo, SecretPayload } from "@/types/secrets";
import { useTranslations } from "next-intl";

/**
 * The inputs one kind of secret needs, generated from the schema the server
 * publishes for it.
 *
 * There are five kinds and there will be more, and each one is a Pydantic model
 * whose fields the backend already describes in JSON Schema at
 * `GET /secrets/kinds`. Five hand-written forms here would be five forms that
 * stop matching the day somebody adds a field - and the failure mode is not a
 * broken build, it is a credential saved without its region that authenticates
 * nowhere.
 *
 * So there is one form, and it is the same generator the Builder uses for
 * capability configuration. What it knows about secrets specifically is two
 * lines of JSON Schema: `const` (a payload states its own `kind`, which is the
 * caller's to supply, not the reader's to answer) and `format: "password"`
 * (every `SecretStr`, masked).
 */
interface SecretFieldsProps {
  /** The kind being entered, with the schema its inputs come from. */
  info: SecretKindInfo;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  disabled?: boolean;
  /** Prefixed onto every field id, so two forms on one page keep distinct labels. */
  idPrefix: string;
  /** What the server said about individual fields, keyed by field name. */
  errors?: Readonly<Record<string, string>>;
  /** Fields fixed by the stored record while replacing its value. */
  hiddenFields?: readonly string[];
}

export function SecretFields({
  info,
  value,
  onChange,
  disabled,
  idPrefix,
  errors,
  hiddenFields = [],
}: SecretFieldsProps) {
  const t = useTranslations("vault");
  const browserCredential = info.kind === "browser_login" || info.kind === "browser_profile";
  const hasTotp = info.kind === "browser_login";
  const accountPolicy = value.account_policy === "named_agents" ? "named_agents" : "personal";
  const customFields = browserCredential
    ? ["account_policy", "authorized_agent_ids", ...(hasTotp ? ["totp"] : [])]
    : [];
  const schema: JsonSchema = {
    ...info.json_schema,
    properties: Object.fromEntries(
      Object.entries(info.json_schema.properties ?? {}).filter(
        ([name]) => !customFields.includes(name) && !hiddenFields.includes(name),
      ),
    ),
  };
  const update = (next: Record<string, unknown>) => {
    if (browserCredential && next.account_policy === "personal") {
      delete next.authorized_agent_ids;
    }
    onChange(next);
  };

  return (
    <div className="space-y-4">
      {/* What the shape is, only where there is more than one field to read
          it against: over a lone API key field it was a sentence about nothing. */}
      {secretFieldNames(info.json_schema).length > 1 && (
        <p className="text-muted-foreground text-xs leading-relaxed">{info.description}</p>
      )}
      <SchemaForm
        schema={schema}
        value={value}
        onChange={update}
        disabled={disabled}
        idPrefix={idPrefix}
        errors={errors}
      />
      {browserCredential && !hiddenFields.includes("account_policy") && (
        <div className="space-y-2">
          <Label htmlFor={`${idPrefix}-account-policy`}>{t("browserAccountPolicy")}</Label>
          <Select
            value={accountPolicy}
            disabled={disabled}
            onValueChange={(next) => update({ ...value, account_policy: next })}
          >
            <SelectTrigger id={`${idPrefix}-account-policy`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="personal">{t("browserPersonalAccount")}</SelectItem>
              <SelectItem value="named_agents">{t("browserSharedAccount")}</SelectItem>
            </SelectContent>
          </Select>
        </div>
      )}
      {hasTotp && (
        <BrowserTotpFields
          idPrefix={idPrefix}
          value={value.totp}
          error={errors?.totp}
          disabled={disabled}
          onChange={(totp) => {
            const next = { ...value };
            if (totp === undefined) delete next.totp;
            else next.totp = totp;
            update(next);
          }}
        />
      )}
      {browserCredential && accountPolicy === "named_agents" && (
        <BrowserAuthorizedAgents
          idPrefix={idPrefix}
          value={value.authorized_agent_ids}
          error={errors?.authorized_agent_ids}
          disabled={disabled}
          onChange={(authorizedAgentIds) =>
            update({ ...value, authorized_agent_ids: authorizedAgentIds })
          }
        />
      )}
    </div>
  );
}

function BrowserTotpFields({
  idPrefix,
  value,
  error,
  disabled,
  onChange,
}: {
  idPrefix: string;
  value: unknown;
  error?: string;
  disabled?: boolean;
  onChange: (value: Record<string, unknown> | undefined) => void;
}) {
  const t = useTranslations("vault");
  const current = isRecord(value) ? value : {};
  const seed = typeof current.seed === "string" ? current.seed : "";
  const totp = (changes: Record<string, unknown>) => ({
    seed,
    algorithm: typeof current.algorithm === "string" ? current.algorithm : "SHA1",
    digits: current.digits === 8 ? 8 : 6,
    period_seconds:
      typeof current.period_seconds === "number" ? current.period_seconds : 30,
    ...changes,
  });
  const seedId = `${idPrefix}-totp-seed`;
  const algorithmId = `${idPrefix}-totp-algorithm`;
  const digitsId = `${idPrefix}-totp-digits`;
  const periodId = `${idPrefix}-totp-period`;
  const errorId = `${seedId}-error`;

  return (
    <fieldset className="min-w-0 space-y-3">
      <legend className="text-sm font-medium">{t("browserTotp")}</legend>
      <div className="space-y-2">
        <Label htmlFor={seedId}>{t("browserTotpSeed")}</Label>
        <Input
          id={seedId}
          type="password"
          autoComplete="off"
          value={seed}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          onChange={(event) => {
            const nextSeed = event.target.value;
            onChange(nextSeed === "" ? undefined : totp({ seed: nextSeed }));
          }}
        />
        {error && (
          <p id={errorId} className="text-destructive text-xs">
            {error}
          </p>
        )}
      </div>
      {seed !== "" && (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor={algorithmId}>{t("browserTotpAlgorithm")}</Label>
            <Select
              value={String(totp({}).algorithm)}
              disabled={disabled}
              onValueChange={(algorithm) => onChange(totp({ algorithm }))}
            >
              <SelectTrigger id={algorithmId}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="SHA1">SHA1</SelectItem>
                <SelectItem value="SHA256">SHA256</SelectItem>
                <SelectItem value="SHA512">SHA512</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor={digitsId}>{t("browserTotpDigits")}</Label>
            <Select
              value={String(totp({}).digits)}
              disabled={disabled}
              onValueChange={(digits) => onChange(totp({ digits: Number(digits) }))}
            >
              <SelectTrigger id={digitsId}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="6">6</SelectItem>
                <SelectItem value="8">8</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor={periodId}>{t("browserTotpPeriod")}</Label>
            <Input
              id={periodId}
              type="number"
              min={15}
              max={120}
              value={String(totp({}).period_seconds)}
              disabled={disabled}
              onChange={(event) => {
                const period = event.target.value;
                onChange(totp({ period_seconds: period === "" ? undefined : Number(period) }));
              }}
            />
          </div>
        </div>
      )}
    </fieldset>
  );
}

function BrowserAuthorizedAgents({
  idPrefix,
  value,
  error,
  disabled,
  onChange,
}: {
  idPrefix: string;
  value: unknown;
  error?: string;
  disabled?: boolean;
  onChange: (value: string[] | undefined) => void;
}) {
  const t = useTranslations("vault");
  const { agents, isLoading, error: loadError } = useAgents();
  const selected = Array.isArray(value)
    ? value.filter((id): id is string => typeof id === "string")
    : [];
  const eligible = agents.filter((agent) => agent.kind === "agent");
  const errorId = `${idPrefix}-authorized-agents-error`;

  return (
    <fieldset
      className="min-w-0 space-y-2"
      aria-invalid={error ? true : undefined}
      aria-describedby={error ? errorId : undefined}
    >
      <legend className="text-sm font-medium">{t("browserAuthorizedAgents")}</legend>
      {isLoading ? (
        <p role="status" className="text-muted-foreground text-sm">
          {t("browserAgentsLoading")}
        </p>
      ) : eligible.length === 0 ? (
        <p role={loadError ? "alert" : "status"} className="text-muted-foreground text-sm">
          {loadError ? t("browserAgentsUnavailable") : t("browserNoAgents")}
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {eligible.map((agent) => {
            const id = `${idPrefix}-authorized-agent-${agent.id}`;
            const checked = selected.includes(agent.id);
            return (
              <label
                key={agent.id}
                htmlFor={id}
                className="border-border flex min-w-0 items-center gap-2 rounded-md border px-3 py-2 text-sm"
              >
                <Checkbox
                  id={id}
                  checked={checked}
                  disabled={disabled}
                  onCheckedChange={(next) => {
                    const ids = next
                      ? [...selected, agent.id]
                      : selected.filter((selectedId) => selectedId !== agent.id);
                    onChange(ids.length === 0 ? undefined : [...new Set(ids)]);
                  }}
                />
                <span className="truncate">{agent.name}</span>
              </label>
            );
          })}
        </div>
      )}
      {error && (
        <p id={errorId} className="text-destructive text-xs">
          {error}
        </p>
      )}
    </fieldset>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The fields a form generated from `schema` actually renders.
 *
 * Used to tell `submitFailure` which of the server's complaints this form can
 * show. Without it a message about `api_version` would be announced in a toast
 * while the input it belongs to sat there unmarked.
 */
export function secretFieldNames(schema: JsonSchema): string[] {
  return Object.entries(schema.properties ?? {})
    .filter(([, property]) => property.const === undefined)
    .map(([name]) => name);
}

/**
 * Whether every required field has an answer.
 *
 * The server refuses an incomplete payload, so this only decides whether the
 * submit button is offered - being told a region is missing before sending
 * beats being told after. A field the form cleared reads back as `undefined`,
 * which is what an untouched one already is.
 */
export function isSecretComplete(
  schema: JsonSchema,
  value: Record<string, unknown>,
  kind?: SecretKind,
): boolean {
  const required = schema.required ?? [];
  const fieldsComplete = secretFieldNames(schema)
    .filter((name) => required.includes(name))
    .every((name) => {
      const answer = value[name];
      return typeof answer === "string" ? answer.trim().length > 0 : answer !== undefined;
    });
  if (!fieldsComplete) return false;
  if (kind === "browser_login" && value.totp !== undefined && value.totp !== null) {
    const totp = isRecord(value.totp) ? value.totp : {};
    const seed = totp.seed;
    const period = totp.period_seconds ?? 30;
    if (
      typeof seed !== "string" ||
      seed.trim() === "" ||
      typeof period !== "number" ||
      !Number.isInteger(period) ||
      period < 15 ||
      period > 120
    )
      return false;
  }
  if (
    (kind === "browser_login" || kind === "browser_profile") &&
    value.account_policy === "named_agents" &&
    (!Array.isArray(value.authorized_agent_ids) || value.authorized_agent_ids.length === 0)
  )
    return false;
  return true;
}

/**
 * The payload for the wire: the discriminator, then what was typed.
 *
 * `kind` is written first and the values spread over it, which is deliberate -
 * a schema cannot contribute a `kind` of its own, because the generated form
 * never renders a `const` field in the first place.
 */
export function toSecretPayload(kind: SecretKind, value: Record<string, unknown>): SecretPayload {
  return { ...value, kind };
}
