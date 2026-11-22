"use client";

import { useTranslations } from "next-intl";

import { useAgents, usePermissions, useUsageStats } from "@/hooks";
import { useAuthStore } from "@/stores";
import { agentTag, filterAgentRows, myAgentsPolicy } from "@/lib/dashboard/my-agents";
import type { Period } from "@/lib/dashboard/period";
import { ROUTES } from "@/lib/constants";
import { WidgetSkeleton } from "../widget-states";
import { CommandPanel, PanelNote, PanelRow } from "./panel";

/** How many rows the preview carries; the rest are a click away, in All apps. */
const SHOWN = 3;

/**
 * Your apps, as one panel: the agents the caller runs most, or - where there
 * are none yet - the agents shared with them, the shelf the old card showed
 * them. The rows are a preview, All apps is the destination.
 */
export function AppsPanel({
  period,
  canRun,
}: {
  period: Period;
  /** Whether the caller may run agents - what the usage read asks. */
  canRun: boolean;
}) {
  const t = useTranslations("dashboard.commandCenter.apps");
  const tAgents = useTranslations("dashboard.widgets.my-agents");
  const tErrors = useTranslations("dashboard.errors");
  const { can } = usePermissions();
  const userId = useAuthStore((state) => state.user?.id ?? null);

  const usage = useUsageStats(
    { from: period.from, to: period.to },
    { scope: "own", enabled: canRun },
  );
  const policy = myAgentsPolicy(can);
  const list = useAgents({ enabled: !canRun });

  const topAgents = (usage.usage?.by_agent ?? []).slice(0, SHOWN);
  const sharedAgents = filterAgentRows(list.agents, policy, userId).slice(0, SHOWN);

  const loading = (canRun && usage.isLoading) || (!canRun && list.isLoading);
  const failed = (canRun && usage.error !== null) || (!canRun && list.error !== null);
  const empty = canRun ? topAgents.length === 0 : sharedAgents.length === 0;

  return (
    <CommandPanel
      title={t("title")}
      description={t("description")}
      go={t("go")}
      href={ROUTES.ARTIFACTS}
    >
      {loading ? (
        <WidgetSkeleton rows={SHOWN} />
      ) : failed ? (
        <PanelNote title={tErrors("title")} description={tErrors("description")} />
      ) : empty ? (
        <PanelNote
          title={canRun ? t("empty.title") : tAgents("empty.title")}
          description={canRun ? t("empty.description") : tAgents("empty.description")}
        />
      ) : canRun ? (
        topAgents.map((row) => (
          <PanelRow
            key={row.agent_id}
            title={row.name}
            subline={tAgents("runs", { count: row.runs })}
          />
        ))
      ) : (
        sharedAgents.map((agent) => (
          <PanelRow key={agent.id} title={agent.name} subline={tAgents(agentTag(agent, userId))} />
        ))
      )}
    </CommandPanel>
  );
}
