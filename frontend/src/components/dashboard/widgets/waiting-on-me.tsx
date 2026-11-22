"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { BackgroundTaskList } from "@/components/chat/background-tasks";
import { NotificationGlyph, NotificationMeta } from "@/components/layout/notification-glyph";
import { useAgentMessageThreads, useNotificationInbox } from "@/hooks";
import { taskIsActive, useConversationTasks } from "@/hooks/use-conversation-tasks";
import { usePendingApprovals } from "@/hooks/use-pending-approvals";
import { ROUTES } from "@/lib/constants";
import { isInAppPath } from "@/lib/notification-link";
import type { Notification } from "@/lib/notifications-api";
import { notificationSummary } from "@/lib/notification-summary";
import { toolLabel } from "@/lib/tool-catalog";
import type { WorkflowApprovalRead } from "@/lib/workflows/types";
import { cn, timeAgo } from "@/lib/utils";
import { Button } from "@/components/ui";
import { AgentFace } from "../agent-face";
import { WidgetFrame } from "../widget-frame";
import { WidgetEmptyBody, WidgetErrorBody, WidgetSkeleton } from "../widget-states";
import type { DashboardWidgetProps } from "./types";
import type { ToolApproval } from "@/types/runs";

/** How many rows one group shows before the "+n more" line. */
const SHOWN_PER_GROUP = 3;

/**
 * Everything waiting on the reader, in one place: the approvals queue, the
 * unread agent threads, the unread notifications and the background tasks
 * still running. Client-side fan-in of the four reads the separate cards
 * already make - no new backend endpoint.
 *
 * The plain-language activity feed (Fix 3) slots into Track beside this card,
 * as a second widget in the section, rather than into this roll-up: this card
 * answers "what waits on me", the feed answers "what happened".
 */
export function WaitingOnMeWidget({ title, hint, seeAll, options }: DashboardWidgetProps) {
  const t = useTranslations("dashboard.widgets.waiting-on-me");
  const tTime = useTranslations("time");
  const tTools = useTranslations("chat.tools");
  const tAgentMessages = useTranslations("agentMessages");
  const tNotifications = useTranslations("notifications");
  const tStop = useTranslations("improvement.stop");
  const locale = useLocale();

  const approvals = usePendingApprovals();
  const threads = useAgentMessageThreads(0, 20);
  const inbox = useNotificationInbox(true);
  const tasks = useConversationTasks(null);

  const loading = approvals.isLoading && threads.isLoading && inbox.isLoading && tasks.isPending;
  // One retry per group, named rather than indexed: an indexed array reads
  // `T | undefined` under `noUncheckedIndexedAccess`, which the group's
  // `failed` prop does not accept.
  const approvalsFailed = approvals.error ? () => approvals.refetch() : null;
  const threadsFailed = threads.error ? () => threads.refetch() : null;
  const inboxFailed = inbox.error ? () => inbox.refetch() : null;
  const tasksFailed = tasks.error ? () => tasks.refetch() : null;
  const allFailed =
    approvalsFailed !== null &&
    threadsFailed !== null &&
    inboxFailed !== null &&
    tasksFailed !== null;

  const approvalRows = [...approvals.tools, ...approvals.steps].slice(0, SHOWN_PER_GROUP);
  const unreadThreads = threads.threads.filter((thread) => thread.unread);
  const unreadNotifications = inbox.notifications.filter((item) => item.read_at === null);
  const activeTasks = (tasks.data ?? []).filter(taskIsActive);

  const empty =
    !loading &&
    !allFailed &&
    approvals.count === 0 &&
    unreadThreads.length === 0 &&
    unreadNotifications.length === 0 &&
    activeTasks.length === 0;

  const refetchAll = () => {
    void approvals.refetch();
    void threads.refetch();
    void inbox.refetch();
    void tasks.refetch();
  };

  return (
    <WidgetFrame title={title} hint={hint} seeAll={seeAll} options={options}>
      {loading ? (
        <WidgetSkeleton />
      ) : allFailed ? (
        <WidgetErrorBody onRetry={refetchAll} />
      ) : empty ? (
        <WidgetEmptyBody
          icon={Inbox}
          title={t("empty.title")}
          description={t("empty.description")}
        />
      ) : (
        <div className="space-y-5">
          <WaitingGroup
            name={t("groups.approvals")}
            count={approvals.count}
            shown={approvalRows.length}
            failed={approvalsFailed}
            retryLabel={t("retry")}
            loadFailed={t("groupLoadFailed")}
          >
            <ul className="space-y-2">
              {approvalRows.map((approval) =>
                "tool_id" in approval ? (
                  <ToolApprovalRow
                    key={approval.id}
                    approval={approval}
                    toolName={toolLabel(approval.tool_id, tTools)}
                    someAgent={t("someAgent")}
                    waiting={
                      approval.created_at
                        ? t("waiting", { ago: timeAgo(approval.created_at, tTime, locale) })
                        : null
                    }
                    review={t("review")}
                  />
                ) : (
                  <StepApprovalRow
                    key={approval.id}
                    step={approval}
                    waiting={
                      approval.created_at
                        ? t("waiting", { ago: timeAgo(approval.created_at, tTime, locale) })
                        : null
                    }
                    review={t("review")}
                  />
                ),
              )}
            </ul>
          </WaitingGroup>
          <WaitingGroup
            name={t("groups.agentMessages")}
            count={unreadThreads.length}
            shown={Math.min(unreadThreads.length, SHOWN_PER_GROUP)}
            failed={threadsFailed}
            retryLabel={t("retry")}
            loadFailed={t("groupLoadFailed")}
          >
            <ul className="space-y-1">
              {unreadThreads.slice(0, SHOWN_PER_GROUP).map((thread) => (
                <li key={thread.id}>
                  <Link
                    href={ROUTES.AGENT_MESSAGES}
                    prefetch={false}
                    className="hover:bg-muted/60 flex items-start gap-3 rounded-md px-1 py-1.5 text-left text-sm"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="sr-only">{tAgentMessages("unread")}</span>
                      <span className="text-foreground block truncate font-medium">
                        {tAgentMessages("threadTitle", {
                          first: thread.agent_a.name,
                          second: thread.agent_b.name,
                        })}
                      </span>
                      <span className="text-muted-foreground block truncate">
                        {thread.last_message
                          ? tAgentMessages("lastMessage", {
                              slug: thread.last_message.sender_agent_slug,
                              body: thread.last_message.body,
                            })
                          : tAgentMessages("noMessagesYet")}
                      </span>
                      <span className="text-muted-foreground block text-xs">
                        {timeAgo(thread.last_message_at ?? thread.updated_at, tTime, locale)}
                      </span>
                    </span>
                    <span aria-hidden className="bg-primary mt-1.5 size-2 shrink-0 rounded-full" />
                  </Link>
                </li>
              ))}
            </ul>
          </WaitingGroup>
          <WaitingGroup
            name={t("groups.notifications")}
            count={unreadNotifications.length}
            shown={Math.min(unreadNotifications.length, SHOWN_PER_GROUP)}
            failed={inboxFailed}
            retryLabel={t("retry")}
            loadFailed={t("groupLoadFailed")}
          >
            <ul className="space-y-1">
              {unreadNotifications.slice(0, SHOWN_PER_GROUP).map((item) => (
                <NotificationRow
                  key={item.id}
                  item={item}
                  tNotifications={tNotifications}
                  tStop={tStop}
                  tTime={tTime}
                  locale={locale}
                  markRead={inbox.markRead}
                />
              ))}
            </ul>
          </WaitingGroup>
          <WaitingGroup
            name={t("groups.backgroundTasks")}
            count={activeTasks.length}
            shown={Math.min(activeTasks.length, SHOWN_PER_GROUP)}
            failed={tasksFailed}
            retryLabel={t("retry")}
            loadFailed={t("groupLoadFailed")}
          >
            <BackgroundTaskList
              tasks={activeTasks.slice(0, SHOWN_PER_GROUP)}
              stopping={tasks.stop.isPending}
              onStop={(id) => tasks.stop.mutate(id)}
            />
          </WaitingGroup>
        </div>
      )}
    </WidgetFrame>
  );
}

/** One source's slice of the card: its heading with the count, its rows, its "+n more". */
function WaitingGroup({
  name,
  count,
  shown,
  failed,
  retryLabel,
  loadFailed,
  children,
}: {
  name: string;
  count: number;
  shown: number;
  failed: (() => void) | null;
  retryLabel: string;
  loadFailed: string;
  children: ReactNode;
}) {
  const t = useTranslations("dashboard.widgets.waiting-on-me");
  if (failed) {
    return (
      <section aria-label={name}>
        <GroupHeading name={name} count={count} />
        <p className="text-muted-foreground flex items-center gap-2 px-1 py-1 text-sm">
          <span className="flex-1">{loadFailed}</span>
          <Button variant="outline" size="sm" onClick={failed}>
            {retryLabel}
          </Button>
        </p>
      </section>
    );
  }
  if (count === 0) return null;
  return (
    <section aria-label={name}>
      <GroupHeading name={name} count={count} />
      {children}
      {count > shown ? (
        <p className="text-muted-foreground px-1 pt-1 text-xs">
          {t("more", { count: count - shown })}
        </p>
      ) : null}
    </section>
  );
}

function GroupHeading({ name, count }: { name: string; count: number }) {
  return (
    <h4 className="text-muted-foreground flex items-baseline gap-2 px-1 pb-1 text-xs font-medium tracking-wide uppercase">
      <span>{name}</span>
      <span className="text-foreground tabular-nums">{count}</span>
    </h4>
  );
}

/** A parked tool call: who is asking, for what, and how long it has waited. */
function ToolApprovalRow({
  approval,
  toolName,
  someAgent,
  waiting,
  review,
}: {
  approval: ToolApproval;
  toolName: string;
  someAgent: string;
  waiting: string | null;
  review: string;
}) {
  return (
    <li className="flex items-center gap-3 text-sm">
      <AgentFace agentId={approval.agent_id} className="size-8" />
      <span className="min-w-0 flex-1">
        <span className="text-foreground block truncate">
          <span className="font-medium">
            {approval.subagent_name ?? approval.agent_name ?? someAgent}
          </span>
          <span className="text-muted-foreground"> · </span>
          {toolName}
          {approval.account ? (
            <span className="text-muted-foreground"> · {approval.account}</span>
          ) : null}
        </span>
        {waiting ? (
          <span className="text-muted-foreground block truncate text-xs">{waiting}</span>
        ) : null}
      </span>
      <Button asChild size="sm">
        <Link href={`${ROUTES.RUNS}?tab=approvals`}>{review}</Link>
      </Button>
    </li>
  );
}

/** A workflow step asking for a decision: its title, and which workflow it is. */
function StepApprovalRow({
  step,
  waiting,
  review,
}: {
  step: WorkflowApprovalRead;
  waiting: string | null;
  review: string;
}) {
  return (
    <li className="flex items-center gap-3 text-sm">
      <span className="min-w-0 flex-1">
        <span className="text-foreground block truncate font-medium">{step.title}</span>
        <span className="text-muted-foreground block truncate text-xs">
          {step.workflow_name}
          {waiting ? ` · ${waiting}` : ""}
        </span>
      </span>
      <Button asChild size="sm">
        <Link href={`${ROUTES.RUNS}?tab=approvals`}>{review}</Link>
      </Button>
    </li>
  );
}

/** An unread notification: its summary, when it arrived, and a way to open it. */
function NotificationRow({
  item,
  tNotifications,
  tStop,
  tTime,
  locale,
  markRead,
}: {
  item: Notification;
  tNotifications: ReturnType<typeof useTranslations>;
  tStop: ReturnType<typeof useTranslations>;
  tTime: ReturnType<typeof useTranslations>;
  locale: string;
  markRead: (id: string) => Promise<void>;
}) {
  const unread = item.read_at === null;
  const content = (
    <>
      <NotificationGlyph item={item} />
      <span className="min-w-0 flex-1">
        <span className="sr-only">{tNotifications(unread ? "rowUnread" : "rowRead")}</span>
        <span
          className={cn(
            "block truncate text-sm",
            unread ? "text-foreground font-medium" : "text-muted-foreground",
          )}
        >
          {notificationSummary(item, tNotifications, tStop)}
        </span>
        <NotificationMeta item={item}>{timeAgo(item.created_at, tTime, locale)}</NotificationMeta>
      </span>
      <span
        aria-hidden
        className={cn(
          "mt-1.5 size-2 shrink-0 rounded-full",
          unread ? "bg-primary" : "bg-transparent",
        )}
      />
    </>
  );
  const rowClassName =
    "hover:bg-muted/60 flex items-start gap-3 rounded-md px-1 py-1.5 text-left text-sm";
  const handleRead = () => {
    if (unread) {
      markRead(item.id).catch(() => {});
    }
  };
  if (!item.context_url) {
    return (
      <li>
        <button
          type="button"
          onClick={handleRead}
          disabled={!unread}
          className={cn(rowClassName, "w-full", !unread && "cursor-default")}
        >
          {content}
        </button>
      </li>
    );
  }
  if (isInAppPath(item.context_url)) {
    return (
      <li>
        <Link
          href={item.context_url}
          prefetch={false}
          onClick={handleRead}
          className={rowClassName}
        >
          {content}
        </Link>
      </li>
    );
  }
  return (
    <li>
      <a href={item.context_url} onClick={handleRead} className={rowClassName}>
        {content}
      </a>
    </li>
  );
}
