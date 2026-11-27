"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { mayApproveCall, useCallLine } from "@/components/runs/approval-cards";
import { Button } from "@/components/ui";
import { browserCredentialApproval } from "@/lib/browser-credential-approval";
import { ROUTES } from "@/lib/constants";
import { toolLabel } from "@/lib/tool-catalog";
import { timeAgo } from "@/lib/utils";
import type { ToolApproval } from "@/types/runs";

import { AgentFace } from "./agent-face";

/** Who a waiting call is for, and since when. */
function useWhoWhen(approval: ToolApproval): string | null {
  const t = useTranslations("dashboard.widgets.approvals");
  const tTime = useTranslations("time");
  const locale = useLocale();
  if (!approval.created_at) return null;
  const ago = timeAgo(approval.created_at, tTime, locale);
  if (approval.asked_in_chat) {
    return t("forChatAccount", { name: approval.asked_in_chat.name, ago });
  }
  return approval.triggered_by_email
    ? t("forPerson", { email: approval.triggered_by_email, ago })
    : t("waiting", { ago });
}

/**
 * One decision in little room: who wants what, for whom, and the answers.
 *
 * Approve and Reject appear only when the card can say what the call does.
 * Otherwise - hidden content, a browser credential, a call with nothing to
 * summarise - the one action is Review, where the arguments are shown in full.
 */
export function NeedsYouCard({
  approval,
  deciding,
  onDecide,
}: {
  approval: ToolApproval;
  deciding: boolean;
  onDecide: (approved: boolean) => void;
}) {
  const t = useTranslations("dashboard.widgets.approvals");
  const tRuns = useTranslations("pages.runs");
  const tTools = useTranslations("chat.tools");
  const line = useCallLine(approval, true);
  const when = useWhoWhen(approval);
  const decidable =
    line !== null &&
    !approval.content_hidden &&
    browserCredentialApproval(approval).kind === "ordinary";

  return (
    <li className="bg-card space-y-2 rounded-lg border p-2.5 text-sm">
      <div className="flex items-start gap-3">
        <AgentFace agentId={approval.agent_id} className="size-8" />
        <div className="min-w-0 flex-1">
          {/* Who is asking and for what, in words: a queue of tool ids
              (`write_memory`) with no actor is one people approve blind. */}
          <p className="text-foreground truncate">
            <span className="font-medium">
              {approval.subagent_name ?? approval.agent_name ?? t("someAgent")}
            </span>
            <span className="text-muted-foreground"> · </span>
            {toolLabel(approval.tool_id, tTools)}
            {approval.account ? (
              <span className="text-muted-foreground"> · {approval.account}</span>
            ) : null}
          </p>
          {line !== null && <p className="text-foreground/80 line-clamp-2 break-words">{line}</p>}
          {when !== null && <p className="text-muted-foreground truncate text-xs">{when}</p>}
        </div>
      </div>
      <div className="flex flex-wrap justify-end gap-2">
        <Button asChild size="sm" variant={decidable ? "ghost" : "default"}>
          <Link href={`${ROUTES.RUNS}?tab=approvals`}>{t("review")}</Link>
        </Button>
        {decidable && (
          <Button size="sm" variant="outline" disabled={deciding} onClick={() => onDecide(false)}>
            {tRuns("reject")}
          </Button>
        )}
        {decidable && mayApproveCall(approval) && (
          <Button size="sm" disabled={deciding} onClick={() => onDecide(true)}>
            {tRuns("approve")}
          </Button>
        )}
      </div>
    </li>
  );
}
