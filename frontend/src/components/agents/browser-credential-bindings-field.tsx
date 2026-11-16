"use client";

import { useTranslations } from "next-intl";

import { Button, Checkbox } from "@/components/ui";
import type { FieldCopy } from "@/components/agents/capability-field-copy";
import { useSecrets } from "@/hooks";
import { useAuthStore } from "@/stores";
import type { Secret } from "@/types/secrets";

const MAX_BROWSER_CREDENTIALS = 50;

interface BrowserCredentialBindingsFieldProps {
  idPrefix: string;
  connection: unknown;
  value: unknown;
  copy: FieldCopy | null;
  error?: string;
  disabled?: boolean;
  onChange: (ids: string[]) => void;
}

/** Let an agent author choose safe Vault metadata instead of copying secret ids. */
export function BrowserCredentialBindingsField({
  idPrefix,
  connection,
  value,
  copy,
  error,
  disabled = false,
  onChange,
}: BrowserCredentialBindingsFieldProps) {
  const t = useTranslations("agents");
  const userId = useAuthStore((state) => state.user?.id ?? null);
  const { secrets, isLoading, listError } = useSecrets();
  const selected = Array.isArray(value)
    ? value.filter((id): id is string => typeof id === "string")
    : [];
  const browserCredentials = secrets.filter((secret) =>
    canBindBrowserCredential(secret, userId),
  );
  const knownIds = new Set(browserCredentials.map((secret) => secret.id));
  const listReady = !isLoading && listError == null;
  const unavailable = listReady ? selected.filter((id) => !knownIds.has(id)) : [];
  const builtIn = connection === "built_in";
  const descriptionId = idPrefix + "-browser-credentials-description";
  const errorId = idPrefix + "-browser-credentials-error";

  return (
    <fieldset
      className="min-w-0 space-y-2"
      aria-invalid={error ? true : undefined}
      aria-describedby={error ? descriptionId + " " + errorId : descriptionId}
    >
      <legend className="text-sm font-medium">
        {copy?.title ?? t("browserCredentialsTitle")}
      </legend>
      <p id={descriptionId} className="text-muted-foreground text-xs">
        {copy?.description ?? t("browserCredentialsDescription")}
      </p>

      {!builtIn && (
        <p className="text-muted-foreground text-sm" role="status">
          {t("browserCredentialsBuiltInOnly")}
        </p>
      )}

      {isLoading ? (
        <p className="text-muted-foreground text-sm" role="status">
          {t("browserCredentialsLoading")}
        </p>
      ) : listError ? (
        <p className="text-destructive text-sm" role="alert">
          {t("browserCredentialsUnavailable")}
        </p>
      ) : browserCredentials.length === 0 ? (
        <p className="text-muted-foreground text-sm" role="status">
          {t("browserCredentialsNone")}
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {browserCredentials.map((secret) => {
            const checkboxId = idPrefix + "-browser-credential-" + secret.id;
            const checked = selected.includes(secret.id);
            const atLimit = !checked && selected.length >= MAX_BROWSER_CREDENTIALS;
            return (
              <label
                key={secret.id}
                htmlFor={checkboxId}
                className="border-border flex min-w-0 items-center gap-2 rounded-md border px-3 py-2 text-sm"
              >
                <Checkbox
                  id={checkboxId}
                  checked={checked}
                  disabled={disabled || (!builtIn && !checked) || atLimit}
                  onCheckedChange={(next) => {
                    const ids = next
                      ? [...selected, secret.id]
                      : selected.filter((id) => id !== secret.id);
                    onChange([...new Set(ids)]);
                  }}
                />
                <span className="min-w-0 truncate">{secret.name}</span>
                <span className="text-muted-foreground ml-auto shrink-0 text-xs">
                  {secret.kind === "browser_login"
                    ? t("browserCredentialLogin")
                    : t("browserCredentialProfile")}
                </span>
              </label>
            );
          })}
        </div>
      )}

      {unavailable.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-destructive text-xs" role="alert">
            {t("browserCredentialSelectionUnavailable", { count: unavailable.length })}
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled}
            onClick={() => onChange(selected.filter((id) => knownIds.has(id)))}
          >
            {t("browserCredentialRemoveUnavailable")}
          </Button>
        </div>
      )}

      {error && (
        <p id={errorId} className="text-destructive text-xs" role="alert">
          {error}
        </p>
      )}
    </fieldset>
  );
}

function canBindBrowserCredential(secret: Secret, userId: string | null): boolean {
  if (secret.kind !== "browser_login" && secret.kind !== "browser_profile") return false;
  if (secret.usage_scope === "personal") {
    return (
      secret.visibility === "private" &&
      userId !== null &&
      secret.owner_user_id === userId
    );
  }
  return (
    secret.usage_scope === "organization" &&
    secret.visibility === "org" &&
    secret.owner_user_id === null
  );
}
