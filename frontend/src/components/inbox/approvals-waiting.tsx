"use client";

import Link from "next/link";
import { useConsoleRouter as useRouter } from "@/hooks/use-console-router";
import { ChevronRight } from "lucide-react";
import { useTranslations } from "next-intl";

import { AnsweredRow, useAnswered } from "@/components/runs/answered-row";
import { PendingApproval } from "@/components/runs/approval-cards";
import { useActionPhrase } from "@/components/runs/approval-request";
import { StepApprovalRow } from "@/components/runs/workflow-approvals-card";
import { useDecideApproval } from "@/hooks/use-runs";
import { useDecideWorkflowApproval } from "@/hooks/use-workflow-approvals";
import { ROUTES } from "@/lib/constants";
import type { WorkflowApprovalRead } from "@/lib/workflows/types";
import type { ToolApproval } from "@/types/runs";

import styles from "./inbox-row.module.css";

const SHOWN = 5;

/** What needs the reader's decision, as cards; an answered one confirms, then folds. */
export function ApprovalsWaiting({
  tools,
  steps,
}: {
  tools: ToolApproval[];
  steps: WorkflowApprovalRead[];
}) {
  const t = useTranslations("inbox.approvals");
  const tRuns = useTranslations("pages.runs");
  const phrase = useActionPhrase();
  const router = useRouter();
  const decideTool = useDecideApproval();
  const decideStep = useDecideWorkflowApproval();
  const deciding = decideTool.isPending || decideStep.isPending;
  const { answered, record, dismiss } = useAnswered();
  const queue = `${ROUTES.RUNS}?tab=approvals`;
  const done = new Set(answered.map((item) => item.id));
  const shownSteps = steps.filter((step) => !done.has(step.id)).slice(0, SHOWN);
  const shownTools = tools.filter((tool) => !done.has(tool.id)).slice(0, SHOWN - shownSteps.length);

  if (answered.length + shownSteps.length + shownTools.length === 0) return null;

  // A refused decision is already toasted by the hook, and leaves its card up.
  const settle = (decision: Promise<unknown>, id: string, title: string, approved: boolean) =>
    void decision.then(
      () => record({ id, title, approved }),
      () => undefined,
    );

  return (
    <section aria-labelledby="inbox-approvals" className="border-b last:border-b-0">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-4 pb-2 sm:px-5">
        <h2 id="inbox-approvals" className="text-muted-foreground text-xs font-medium">
          {t("title")}
        </h2>
        <Link
          href={queue}
          className="text-muted-foreground hover:text-foreground touch:min-h-11 inline-flex min-h-6 items-center gap-1 rounded-sm text-xs focus-visible:outline-2 focus-visible:outline-offset-4"
        >
          {t("open")}
          <ChevronRight className="size-3.5" aria-hidden />
        </Link>
      </div>
      <ul className="[&>li]:bg-card space-y-2 px-3 pb-3 sm:px-4 [&>li]:overflow-hidden [&>li]:rounded-xl [&>li]:border">
        {answered.map((item) => (
          <AnsweredRow key={item.id} item={item} onGone={dismiss} />
        ))}
        {shownSteps.map((approval) => (
          <li key={approval.id}>
            <StepApprovalRow
              className={styles.approvalRow}
              approval={approval}
              deciding={deciding}
              onDecide={(approved, final, choice, note) =>
                settle(
                  decideStep.mutateAsync({ id: approval.id, approved, final, choice, note }),
                  approval.id,
                  approval.title,
                  approved,
                )
              }
            />
          </li>
        ))}
        {shownTools.map((approval) => (
          <PendingApproval
            className={styles.approvalRow}
            key={approval.id}
            approval={approval}
            deciding={deciding}
            onDecide={(approved, note) =>
              settle(
                decideTool.mutateAsync({ id: approval.id, approved, note }),
                approval.id,
                `${approval.subagent_name ?? approval.agent_name ?? tRuns("anAgent")} · ${phrase(approval.tool_id)}`,
                approved,
              )
            }
            onOpenRun={() => router.push(`${queue}&run=${encodeURIComponent(approval.run_id)}`)}
          />
        ))}
      </ul>
    </section>
  );
}
