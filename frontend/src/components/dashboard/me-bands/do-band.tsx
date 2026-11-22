"use client";

import { Check } from "lucide-react";
import { useTranslations } from "next-intl";

import { urgentFirst } from "@/components/chat/suggestion-actions";
import { useAgentAttention, useOrganizations } from "@/hooks";
import { useSuggestions } from "@/hooks/use-suggestions";
import { attentionParts } from "@/lib/attention";
import { ROUTES } from "@/lib/constants";
import type { Period } from "@/lib/dashboard/period";
import { WidgetSkeleton } from "../widget-states";
import { BandCard, BandNote, BandRow } from "./band-card";

/** How many rows the preview carries; the rest are a click away, in the inbox. */
const SHOWN = 3;

/**
 * Do, as one card: the briefing first - what cannot wait, from the nightly
 * look - and the agents that need attention filling the rows behind it. The
 * rows are a preview only: the card lands in the inbox, where each proposal
 * is answered, and the agents are looked at from their own pages.
 */
export function DoBand({
  period,
  canRun,
  canViewRuns,
}: {
  period: Period;
  /** Whether the caller may run agents - what the briefing itself asks. */
  canRun: boolean;
  /** Whether the caller reads every run - what the attention list asks. */
  canViewRuns: boolean;
}) {
  const t = useTranslations("dashboard.bands");
  const tSections = useTranslations("dashboard.sections");
  const tSugg = useTranslations("dashboard.widgets.suggestions");
  const tParts = useTranslations("attention");
  const tErrors = useTranslations("dashboard.errors");

  const sugg = useSuggestions(canRun);
  const attention = useAgentAttention(
    { from: period.from, to: period.to },
    { enabled: canViewRuns },
  );
  const { activeOrg } = useOrganizations();
  const on = activeOrg?.proactive_enabled === true || activeOrg?.proactive_watches_enabled === true;

  const suggestions = urgentFirst(sugg.suggestions);
  const agents = attention.agents;
  const rows = [
    ...suggestions.slice(0, SHOWN).map((suggestion) => ({
      key: suggestion.id,
      title: suggestion.title,
      subline: suggestion.detail ?? undefined,
    })),
    ...agents.slice(0, Math.max(0, SHOWN - suggestions.length)).map((agent) => ({
      key: agent.agent_id,
      title: agent.name,
      subline: attentionParts(agent, tParts).join(" · ") || undefined,
    })),
  ];

  const loading = sugg.isLoading && attention.isLoading;
  const failed = sugg.error !== null && attention.error !== null;
  const empty = suggestions.length === 0 && agents.length === 0;

  return (
    <BandCard
      href={ROUTES.INBOX}
      icon={Check}
      title={tSections("do")}
      description={t("do.description")}
      go={t("do.go")}
    >
      {loading ? (
        <WidgetSkeleton rows={SHOWN} />
      ) : failed ? (
        <BandNote title={tErrors("title")} description={tErrors("description")} />
      ) : empty ? (
        <BandNote
          title={tSugg("empty.title")}
          description={on ? tSugg("empty.description") : tSugg("empty.descriptionOff")}
        />
      ) : (
        rows.map((row) => <BandRow key={row.key} title={row.title} subline={row.subline} />)
      )}
    </BandCard>
  );
}
