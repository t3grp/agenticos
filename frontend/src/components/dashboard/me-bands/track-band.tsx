"use client";

import { ChartColumn } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { useAgentMessageThreads, useNotificationInbox, useUsageStats } from "@/hooks";
import { taskIsActive, useConversationTasks } from "@/hooks/use-conversation-tasks";
import { usePendingApprovals } from "@/hooks/use-pending-approvals";
import { ROUTES } from "@/lib/constants";
import type { Period } from "@/lib/dashboard/period";
import { completedShare, formatCompletedShare, statusTally } from "@/lib/run-outcomes";
import { formatUsd } from "../format";
import { WidgetSkeleton } from "../widget-states";
import { BandCard, BandNote, BandRow } from "./band-card";

/** How many rows the preview carries; the rest are a click away, on Activity. */
const SHOWN = 3;

/**
 * Track, as one card: what waits on the reader, fanned in from the four
 * places that hold it, and the period's figures beside it - the runs, what
 * they cost, how many finished. The usage hero that used to sit above the
 * bands is folded in here: its figures are these rows, so the page no
 * longer says them twice.
 *
 * There is no "time saved" row: no endpoint counts it, and the demo's figure
 * has nothing behind it. The finished share is the third row instead.
 */
export function TrackBand({ period }: { period: Period }) {
  const t = useTranslations("dashboard.bands");
  const tSections = useTranslations("dashboard.sections");
  const tSummary = useTranslations("dashboard.widgets.my-summary");
  const tWaiting = useTranslations("dashboard.widgets.waiting-on-me");
  const tErrors = useTranslations("dashboard.errors");
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

  // The waiting line, part by part - the same shape `attentionParts` builds,
  // so a zero part is left out rather than printed as "0 approvals".
  const waitingParts = [
    approvals.count > 0 ? t("track.approvals", { count: approvals.count }) : null,
    unreadThreads.length > 0 ? t("track.messages", { count: unreadThreads.length }) : null,
    unreadNotifications.length > 0
      ? t("track.notifications", { count: unreadNotifications.length })
      : null,
    activeTasks.length > 0 ? t("track.tasks", { count: activeTasks.length }) : null,
  ].filter((part): part is string => part !== null);

  // Every source still loading, like the roll-up this card folds in: one
  // answer is enough to draw the rows, and one failure is not the page's.
  const loading =
    usageLoading && approvals.isLoading && threads.isLoading && inbox.isLoading && tasks.isPending;
  const failed =
    usageError !== null &&
    approvals.error !== null &&
    threads.error !== null &&
    inbox.error !== null &&
    tasks.error !== null;
  const empty = waiting === 0 && totalRuns === 0;

  return (
    <BandCard
      href={ROUTES.RUNS}
      icon={ChartColumn}
      title={tSections("track")}
      description={t("track.description")}
      go={t("track.go")}
    >
      {loading ? (
        <WidgetSkeleton rows={SHOWN} />
      ) : failed ? (
        <BandNote title={tErrors("title")} description={tErrors("description")} />
      ) : empty ? (
        <BandNote title={tWaiting("empty.title")} description={tWaiting("empty.description")} />
      ) : (
        <>
          <BandRow
            title={
              waiting > 0 ? t("track.waiting", { count: waiting }) : tSummary("nothingWaiting")
            }
            subline={waitingParts.length > 0 ? waitingParts.join(" · ") : undefined}
          />
          <BandRow
            title={t("track.runs", { count: totalRuns })}
            subline={tSummary("spentLabel", {
              amount: formatUsd(usage?.cost?.period_usd, locale),
            })}
          />
          <BandRow
            title={t("track.finished", {
              share: formatCompletedShare(
                completedShare(statusTally(usage?.by_status ?? [])),
                locale,
              ),
            })}
            subline={tSummary("finishedOf", { total: totalRuns })}
          />
        </>
      )}
    </BandCard>
  );
}
