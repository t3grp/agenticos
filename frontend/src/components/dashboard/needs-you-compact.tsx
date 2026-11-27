"use client";

import { ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";

import { AnsweredRow, useAnswered } from "@/components/runs/answered-row";
import { useApprovals } from "@/hooks";
import { useDecideApproval } from "@/hooks/use-runs";
import { toolLabel } from "@/lib/tool-catalog";

import { NeedsYouCard } from "./needs-you-card";
import { WidgetEmptyBody, WidgetErrorBody, WidgetSkeleton } from "./widget-states";

const SHOWN = 3;

/**
 * The compact "needs you" list. It reads its own data and carries no frame, so
 * any home can hold it. The caller gates it on `approvals:decide`.
 */
export function NeedsYouCompact() {
  const t = useTranslations("dashboard.widgets.approvals");
  const tTools = useTranslations("chat.tools");
  const { approvals, total, isLoading, error, refetch } = useApprovals();
  const decide = useDecideApproval();
  const { answered, record, dismiss } = useAnswered();

  if (isLoading) return <WidgetSkeleton />;
  if (error) return <WidgetErrorBody onRetry={() => refetch()} />;

  const done = new Set(answered.map((item) => item.id));
  const waiting = approvals.filter((approval) => !done.has(approval.id));
  if (waiting.length + answered.length === 0) {
    return (
      <WidgetEmptyBody
        icon={ShieldCheck}
        title={t("empty.title")}
        description={t("empty.description")}
      />
    );
  }

  return (
    <div className="flex h-full flex-col justify-between gap-2">
      <ul className="space-y-2">
        {answered.map((item) => (
          <AnsweredRow key={item.id} item={item} onGone={dismiss} />
        ))}
        {waiting.slice(0, SHOWN).map((approval) => (
          <NeedsYouCard
            key={approval.id}
            approval={approval}
            deciding={decide.isPending}
            onDecide={(approved) =>
              void decide.mutateAsync({ id: approval.id, approved }).then(
                () =>
                  record({
                    id: approval.id,
                    title: `${approval.subagent_name ?? approval.agent_name ?? t("someAgent")} · ${toolLabel(approval.tool_id, tTools)}`,
                    approved,
                  }),
                // The hook toasts a refusal, and the card stays up.
                () => undefined,
              )
            }
          />
        ))}
      </ul>
      {total > SHOWN ? (
        <p className="text-muted-foreground text-xs">{t("more", { count: total - SHOWN })}</p>
      ) : null}
    </div>
  );
}
