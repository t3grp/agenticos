"use client";

import { useLocale, useTranslations } from "next-intl";

import { useAgentMessageThreads, useNotificationInbox, useUsageStats } from "@/hooks";
import { taskIsActive, useConversationTasks } from "@/hooks/use-conversation-tasks";
import { usePendingApprovals } from "@/hooks/use-pending-approvals";
import type { Period } from "@/lib/dashboard/period";
import { formatUsd } from "../format";

/**
 * The status strip: what waits on the reader, what waits on a decision, what
 * ran and what it cost - the four numbers the whole page answers to, on one
 * line, before anything asks anything.
 *
 * There is no time-saved cell: no endpoint counts it, and the demo's figure
 * has nothing behind it. The finished share lives in the Spend panel instead.
 *
 * Mounted only for a caller who runs agents - the command center gates it on
 * the conversations read - so a viewer asks no usage or approvals question
 * at all.
 */
export function StatusStrip({ period }: { period: Period }) {
  const t = useTranslations("dashboard.commandCenter.strip");
  const locale = useLocale();

  const approvals = usePendingApprovals();
  const threads = useAgentMessageThreads(0, 20);
  const inbox = useNotificationInbox(true);
  const tasks = useConversationTasks(null);
  const {
    usage,
    isLoading: usageLoading,
    error: usageError,
  } = useUsageStats({ from: period.from, to: period.to }, { scope: "own" });

  const unreadThreads = threads.threads.filter((thread) => thread.unread);
  const unreadNotifications = inbox.notifications.filter((item) => item.read_at === null);
  const activeTasks = (tasks.data ?? []).filter(taskIsActive);
  const waiting =
    approvals.count + unreadThreads.length + unreadNotifications.length + activeTasks.length;
  const totalRuns = usage?.total_runs ?? 0;

  // Every source still loading, like the roll-up this strip folds in: one
  // answer is enough to draw the cells, and one failure is not the page's.
  const loading =
    usageLoading && approvals.isLoading && threads.isLoading && inbox.isLoading && tasks.isPending;
  const failed =
    usageError !== null &&
    approvals.error !== null &&
    threads.error !== null &&
    inbox.error !== null &&
    tasks.error !== null;

  const cells = [
    { label: t("waitingLabel"), value: waiting.toLocaleString(locale) },
    { label: t("approvalsLabel"), value: approvals.count.toLocaleString(locale) },
    { label: t("runsLabel"), value: totalRuns.toLocaleString(locale) },
    { label: t("spentLabel"), value: formatUsd(usage?.cost?.period_usd, locale) },
  ];

  if (loading) {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label={t("loading")}
        className="grid grid-cols-2 gap-px overflow-hidden rounded-2xl border sm:grid-cols-4"
      >
        {cells.map((cell) => (
          <div key={cell.label} className="bg-card space-y-2 px-5 py-4">
            <div className="bg-muted h-7 w-16 animate-pulse rounded" />
            <div className="bg-muted h-4 w-24 animate-pulse rounded" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-2xl border sm:grid-cols-4">
      {cells.map((cell) => (
        <div key={cell.label} className="bg-card px-5 py-4">
          <span className="text-foreground block text-2xl font-bold tracking-tight">
            {failed ? "—" : cell.value}
          </span>
          <span className="text-muted-foreground text-sm">{cell.label}</span>
        </div>
      ))}
    </div>
  );
}
