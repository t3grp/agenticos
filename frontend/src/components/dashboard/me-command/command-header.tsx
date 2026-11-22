"use client";

import type { ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";

import { PageHeader } from "../page-header";

/**
 * The command center's heading: the day, the name of the page, and the
 * primary action beside it. No greeting, no summary sentence - the status
 * strip below says the figures, once.
 */
export function CommandHeader({ actions }: { actions?: ReactNode }) {
  const t = useTranslations("dashboard.commandCenter");
  const locale = useLocale();
  const now = new Date();

  return (
    <PageHeader
      className="mb-4"
      title={
        <span className="flex flex-col">
          <span className="text-muted-foreground text-sm font-normal" suppressHydrationWarning>
            {new Intl.DateTimeFormat(locale, {
              weekday: "long",
              day: "numeric",
              month: "long",
            }).format(now)}
          </span>
          <span suppressHydrationWarning>{t("title")}</span>
        </span>
      }
      actions={actions}
    />
  );
}
