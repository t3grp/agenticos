"use client";

import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
} from "@/components/ui";
import { CostCell, ShareCell } from "@/components/runs/spend-tab";
import { ProviderIcon } from "@/components/vault/provider-icon";
import { providerBilling, type ProviderBilling } from "./provider-billing";
import type { CostByProvider } from "@/types/runs";

/**
 * This month's estimated spend, one row per provider, with the link that
 * closes the loop: each provider's own billing console, where the invoice
 * actually lives.
 *
 * The rows come from the same `GET /spend` answer as the Spend tab, asked
 * over the calendar month rather than the dashboard's rolling window — a
 * provider's month is the month its invoice covers. A row whose provider was
 * never recorded is kept and muted rather than dropped: the money was spent
 * either way. A provider with no billing link on file says so instead of
 * linking at a guess, and a self-hosted or subscription provider says why
 * there is no vendor bill at all.
 */
export function ProviderSpend({
  rows,
  providerNames,
  partialRunCount,
}: {
  rows: readonly CostByProvider[];
  providerNames: ReadonlyMap<string, string>;
  /** Runs in the window whose cost is a floor — see `CostSummary.partial_run_count`. */
  partialRunCount: number;
}) {
  const t = useTranslations("pages.finance");
  const total = rows.reduce((sum, row) => sum + Number(row.cost_usd), 0);
  const sorted = [...rows].sort((a, b) => Number(b.cost_usd) - Number(a.cost_usd));

  return (
    <Card>
      <CardHeader className="space-y-1 px-5 pt-4 pb-2">
        <CardTitle className="text-sm">{t("providersTitle")}</CardTitle>
        <CardDescription className="text-xs">{t("providersDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        {partialRunCount > 0 ? (
          <p className="text-muted-foreground px-5 pb-3 text-sm" role="note">
            {t("unpricedRunsNote", { count: partialRunCount })}
          </p>
        ) : null}
        <DataTable<CostByProvider>
          columns={[
            {
              key: "provider",
              header: t("providerColumn"),
              className: "pl-5",
              cell: (row) =>
                row.provider === null ? (
                  <span className="text-muted-foreground italic">{t("notRecorded")}</span>
                ) : (
                  <span className="flex items-center gap-2">
                    <ProviderIcon provider={row.provider} className="h-4 w-4" />
                    {providerNames.get(row.provider) ?? row.provider}
                  </span>
                ),
            },
            {
              key: "runs",
              hideBelow: "sm",
              header: t("runsColumn"),
              align: "right",
              cell: (row) => (
                <span className="font-mono text-xs tabular-nums">{row.run_count}</span>
              ),
            },
            {
              key: "share",
              hideBelow: "sm",
              header: t("share"),
              align: "right",
              cell: (row) => <ShareCell cost={row.cost_usd} total={total} />,
            },
            {
              key: "cost",
              header: t("cost"),
              align: "right",
              cell: (row) => <CostCell cost={row.cost_usd} title={t("theCostIsAFloor")} />,
            },
            {
              key: "billing",
              header: t("billingColumn"),
              align: "right",
              className: "pr-5",
              cell: (row) => (
                <BillingCell
                  billing={providerBilling(row.provider)}
                  name={
                    row.provider === null
                      ? t("notRecorded")
                      : (providerNames.get(row.provider) ?? row.provider)
                  }
                />
              ),
            },
          ]}
          rows={sorted}
          getRowKey={(row) => row.provider ?? "unrecorded"}
          empty={t("nothingSpentYet")}
          className="rounded-none border-0 bg-transparent"
        />
      </CardContent>
    </Card>
  );
}

/** The provider's own billing, or the honest reason there is none to open. */
function BillingCell({ billing, name }: { billing: ProviderBilling; name: string }) {
  const t = useTranslations("pages.finance");
  if (billing.kind === "console") {
    return (
      <a
        href={billing.href}
        target="_blank"
        rel="noreferrer"
        className="text-primary inline-flex items-center gap-1 text-xs underline underline-offset-2"
      >
        {t("openBilling", { provider: name })}
        <ExternalLink className="h-3 w-3" aria-hidden />
      </a>
    );
  }
  return (
    <span className="text-muted-foreground text-xs">
      {billing.kind === "self-hosted"
        ? t("selfHostedNote")
        : billing.kind === "subscription"
          ? t("subscriptionNote")
          : t("noBillingLink")}
    </span>
  );
}
