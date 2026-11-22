"use client";

import { MessageSquare } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { useAgents, usePermissions, useRecentConversations, useUsageStats } from "@/hooks";
import { useAuthStore } from "@/stores";
import { agentTag, filterAgentRows, myAgentsPolicy } from "@/lib/dashboard/my-agents";
import { ROUTES } from "@/lib/constants";
import type { Period } from "@/lib/dashboard/period";
import { timeAgo } from "@/lib/utils";
import { WidgetSkeleton } from "../widget-states";
import { BandCard, BandNote, BandRow } from "./band-card";

/** How many rows the preview carries; the rest are a click away, in chat. */
const SHOWN = 3;

/**
 * Ask, as one card: the recent conversations to continue, or - where there
 * are none yet - the agents the caller runs most. A caller who may not run
 * agents sees the agents shared with them instead, the shelf the old card
 * showed them: the rows are the preview, chat is the destination, and the
 * card is the one click between them.
 */
export function AskBand({ period, canRun }: { period: Period; canRun: boolean }) {
  const t = useTranslations("dashboard.bands");
  const tSections = useTranslations("dashboard.sections");
  const tConv = useTranslations("dashboard.widgets.conversations");
  const tAgents = useTranslations("dashboard.widgets.my-agents");
  const tErrors = useTranslations("dashboard.errors");
  const tTime = useTranslations("time");
  const locale = useLocale();
  const { can } = usePermissions();
  const userId = useAuthStore((state) => state.user?.id ?? null);

  const conv = useRecentConversations(SHOWN, { enabled: canRun });
  const usage = useUsageStats(
    { from: period.from, to: period.to },
    { scope: "own", enabled: canRun },
  );
  const policy = myAgentsPolicy(can);
  const list = useAgents({ enabled: !canRun });

  const conversations = conv.conversations;
  const topAgents = (usage.usage?.by_agent ?? []).slice(0, SHOWN);
  const sharedAgents = filterAgentRows(list.agents, policy, userId).slice(0, SHOWN);

  // The preview is whichever source answered: conversations first, the most-run
  // agents where there are no conversations yet, the shared shelf where the
  // caller may not run agents at all. A source still loading holds the card;
  // a source that failed leaves the others to answer.
  const loading =
    (canRun && conv.isLoading) ||
    (canRun && conversations.length === 0 && usage.isLoading) ||
    (!canRun && list.isLoading);
  const failed =
    (canRun && conv.error !== null && usage.error !== null) || (!canRun && list.error !== null);
  const empty = canRun
    ? conversations.length === 0 && topAgents.length === 0
    : sharedAgents.length === 0;

  return (
    <BandCard
      href={ROUTES.CHAT}
      icon={MessageSquare}
      title={tSections("ask")}
      description={t("ask.description")}
      go={t("ask.go")}
    >
      {loading ? (
        <WidgetSkeleton rows={SHOWN} />
      ) : failed ? (
        <BandNote title={tErrors("title")} description={tErrors("description")} />
      ) : empty ? (
        <BandNote
          title={canRun ? tConv("empty.title") : tAgents("empty.title")}
          description={canRun ? tConv("empty.description") : tAgents("empty.description")}
        />
      ) : canRun && conversations.length > 0 ? (
        conversations.map((conversation) => (
          <BandRow
            key={conversation.id}
            title={conversation.title ?? tConv("untitled")}
            subline={timeAgo(conversation.updated_at || conversation.created_at, tTime, locale)}
          />
        ))
      ) : canRun ? (
        topAgents.map((row) => (
          <BandRow
            key={row.agent_id}
            title={row.name}
            subline={tAgents("runs", { count: row.runs })}
          />
        ))
      ) : (
        sharedAgents.map((agent) => (
          <BandRow key={agent.id} title={agent.name} subline={tAgents(agentTag(agent, userId))} />
        ))
      )}
    </BandCard>
  );
}
