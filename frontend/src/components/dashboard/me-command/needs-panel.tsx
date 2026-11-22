"use client";

import { useTranslations } from "next-intl";

import { useAgentMessageThreads, useNotificationInbox } from "@/hooks";
import { taskIsActive, useConversationTasks } from "@/hooks/use-conversation-tasks";
import { usePendingApprovals } from "@/hooks/use-pending-approvals";
import { ROUTES } from "@/lib/constants";
import { WidgetSkeleton } from "../widget-states";
import { CommandPanel, PanelNote, PanelRow } from "./panel";

/** How many rows the preview carries; the rest are a click away, in the inbox. */
const SHOWN = 3;

/**
 * Needs you, as one panel: the decisions only the reader can make - the
 * approvals waiting on a yes or no, the unread agent messages, the background
 * tasks still running - fanned in from the four places that hold them. A
 * zero is a row, not an empty state: "nothing waiting" is the news.
 */
export function NeedsPanel() {
  const t = useTranslations("dashboard.commandCenter.needs");
  const tErrors = useTranslations("dashboard.errors");

  const approvals = usePendingApprovals();
  const threads = useAgentMessageThreads(0, 20);
  const inbox = useNotificationInbox(true);
  const tasks = useConversationTasks(null);

  const unreadMessages =
    threads.threads.filter((thread) => thread.unread).length +
    inbox.notifications.filter((item) => item.read_at === null).length;
  const activeTasks = (tasks.data ?? []).filter(taskIsActive).length;

  const loading = approvals.isLoading && threads.isLoading && inbox.isLoading && tasks.isPending;
  const failed =
    approvals.error !== null &&
    threads.error !== null &&
    inbox.error !== null &&
    tasks.error !== null;

  return (
    <CommandPanel
      title={t("title")}
      description={t("description")}
      go={t("go")}
      href={ROUTES.INBOX}
    >
      {loading ? (
        <WidgetSkeleton rows={SHOWN} />
      ) : failed ? (
        <PanelNote title={tErrors("title")} description={tErrors("description")} />
      ) : (
        <>
          <PanelRow
            title={t("approvalsTitle")}
            subline={t("approvals", { count: approvals.count })}
          />
          <PanelRow title={t("messagesTitle")} subline={t("messages", { count: unreadMessages })} />
          <PanelRow title={t("tasksTitle")} subline={t("tasks", { count: activeTasks })} />
        </>
      )}
    </CommandPanel>
  );
}
