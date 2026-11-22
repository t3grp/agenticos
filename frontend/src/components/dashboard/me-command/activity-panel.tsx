"use client";

import { useLocale, useTranslations } from "next-intl";

import { useRecentConversations } from "@/hooks";
import { ROUTES } from "@/lib/constants";
import { timeAgo } from "@/lib/utils";
import { WidgetSkeleton } from "../widget-states";
import { CommandPanel, PanelNote, PanelRow } from "./panel";

/** How many rows the preview carries; the rest are a click away, on Activity. */
const SHOWN = 3;

/**
 * Latest activity, as one panel: the newest conversations, what the agents
 * did most recently, in plain words. The rows are a preview only - the runs
 * page is where each one opens.
 */
export function ActivityPanel({
  canRun,
}: {
  /** Whether the caller may run agents - what the conversations read asks. */
  canRun: boolean;
}) {
  const t = useTranslations("dashboard.commandCenter.activity");
  const tConv = useTranslations("dashboard.widgets.conversations");
  const tErrors = useTranslations("dashboard.errors");
  const tTime = useTranslations("time");
  const locale = useLocale();

  const conv = useRecentConversations(SHOWN, { enabled: canRun });
  const conversations = conv.conversations;

  return (
    <CommandPanel title={t("title")} description={t("description")} go={t("go")} href={ROUTES.RUNS}>
      {conv.isLoading ? (
        <WidgetSkeleton rows={SHOWN} />
      ) : conv.error !== null ? (
        <PanelNote title={tErrors("title")} description={tErrors("description")} />
      ) : conversations.length === 0 ? (
        <PanelNote title={tConv("empty.title")} description={tConv("empty.description")} />
      ) : (
        conversations.map((conversation) => (
          <PanelRow
            key={conversation.id}
            title={conversation.title ?? tConv("untitled")}
            subline={timeAgo(conversation.updated_at || conversation.created_at, tTime, locale)}
          />
        ))
      )}
    </CommandPanel>
  );
}
