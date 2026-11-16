"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import type { BrowserCommand } from "@/hooks/use-live-browse";
import type { BrowserCredentialConfirmation } from "@/lib/browse";

/** A person-only decision for one saved credential and the fields it will fill. */
export function BrowserCredentialConfirmationCard({
  confirmation,
  send,
}: {
  confirmation: BrowserCredentialConfirmation;
  send: (command: BrowserCommand) => void;
}) {
  const t = useTranslations("browse");
  const [decided, setDecided] = useState(false);
  const titleId = `browser-credential-confirmation-${confirmation.id}`;

  function decide(type: "confirm_fill" | "cancel_fill") {
    if (decided) return;
    setDecided(true);
    send({ type, confirmation_id: confirmation.id });
  }

  return (
    <section
      aria-labelledby={titleId}
      aria-live="assertive"
      aria-busy={decided}
      className="border-border bg-muted/30 shrink-0 space-y-2 border-b px-3 py-3"
    >
      <h2 id={titleId} className="text-sm font-semibold">
        {t("credentialFillTitle")}
      </h2>
      <p className="text-muted-foreground text-sm">{t("credentialFillDescription")}</p>
      <dl className="grid gap-x-3 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
        <dt className="text-muted-foreground">{t("credentialFillCredential")}</dt>
        <dd className="break-words">{confirmation.credential}</dd>
        <dt className="text-muted-foreground">{t("credentialFillOrigin")}</dt>
        <dd className="break-all font-mono text-xs">{confirmation.origin}</dd>
        <dt className="text-muted-foreground">{t("credentialFillFields")}</dt>
        <dd>
          <ul className="list-inside list-disc">
            {confirmation.fields.map((field) => (
              <li key={field}>{field}</li>
            ))}
          </ul>
        </dd>
      </dl>
      <div className="flex flex-wrap gap-2 pt-1">
        <button
          type="button"
          disabled={decided}
          onClick={() => decide("confirm_fill")}
          className="bg-foreground text-background hover:bg-foreground/90 rounded-md px-3 py-2 text-sm font-medium disabled:opacity-50"
        >
          {t("confirmFill")}
        </button>
        <button
          type="button"
          disabled={decided}
          onClick={() => decide("cancel_fill")}
          className="border-border hover:bg-muted rounded-md border px-3 py-2 text-sm font-medium disabled:opacity-50"
        >
          {t("cancelFill")}
        </button>
      </div>
    </section>
  );
}
