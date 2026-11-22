"use client";

import { useLocale, useTranslations } from "next-intl";

import { useUsageStats } from "@/hooks";
import type { Period } from "@/lib/dashboard/period";
import { ROUTES } from "@/lib/constants";
import { completedShare, formatCompletedShare, statusTally } from "@/lib/run-outcomes";
import { formatUsd } from "../format";
import { WidgetSkeleton } from "../widget-states";
import { CommandPanel, PanelNote, PanelRow } from "./panel";

/**
 * Spend, as one panel: what the work cost, without saying the strip's figures
 * twice. The strip already carries the period's runs and spend, so this panel
 * carries what those two do not say - the cost per run, how many finished,
 * and the agent that ran most.
 */
export function SpendPanel({ period }: { period: Period }) {
  const t = useTranslations("dashboard.commandCenter.spend");
  const tErrors = useTranslations("dashboard.errors");
  const locale = useLocale();

  const { usage, isLoading, error } = useUsageStats(
    { from: period.from, to: period.to },
    { scope: "own" },
  );

  const totalRuns = usage?.total_runs ?? 0;
  const costUsd = Number(usage?.cost?.period_usd ?? 0);
  // No branch for an empty window: the empty note below owns that case, and
  // dividing by one instead of zero keeps the rate defined without a fork.
  const perRun = costUsd / Math.max(totalRuns, 1);
  const topAgent = (usage?.by_agent ?? [])[0];
  const share = formatCompletedShare(completedShare(statusTally(usage?.by_status ?? [])), locale);

  return (
    <CommandPanel title={t("title")} description={t("description")} go={t("go")} href={ROUTES.RUNS}>
      {isLoading ? (
        <WidgetSkeleton rows={3} />
      ) : error !== null ? (
        <PanelNote title={tErrors("title")} description={tErrors("description")} />
      ) : totalRuns === 0 ? (
        <PanelNote title={t("empty.title")} description={t("empty.description")} />
      ) : (
        <>
          <PanelRow
            title={t("perRun", { amount: formatUsd(perRun, locale) })}
            subline={t("perRunSub")}
          />
          <PanelRow title={t("finished", { share })} />
          {topAgent ? (
            <PanelRow title={topAgent.name} subline={t("runs", { count: topAgent.runs })} />
          ) : null}
        </>
      )}
    </CommandPanel>
  );
}
