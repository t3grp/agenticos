"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { Badge, Button, Card, CardContent, Input, Label, LineDiff } from "@/components/ui";
import type { ProposalRead } from "@/lib/failure-learning-api";

import { TryView } from "./try-view";

/**
 * One proposal, read as the approver will read it: the change itself, the
 * eval evidence that backs it, and what applying it will do. A skill
 * target is never eval-gated - no try can prove a skill safe - and it
 * reaches every agent the skill is bound to, with no rollback, so the
 * review says so plainly. Applying is the approval, and an instruction
 * target only applies once its try has passed the gate.
 */
export function ProposalReview({
  proposal,
  canApplyInstructions,
  canApplySkill,
  canReject,
  onApply,
  onReject,
}: {
  proposal: ProposalRead;
  canApplyInstructions: boolean;
  canApplySkill: boolean;
  canReject: boolean;
  onApply: () => void;
  onReject: (reason: string) => void;
}) {
  const t = useTranslations("failureLearning");
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  // The evidence is embedded in the proposal: the try as it stood when the
  // proposal was read, so the review needs no second fetch.
  const evidence = proposal.eval_evidence;
  const isSkill = proposal.target_type === "skill";
  const canApply = isSkill ? canApplySkill : canApplyInstructions;
  const gatePassed = evidence?.gate_passed === true;

  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={proposal.status === "applied" ? "default" : "secondary"}>
            {t(
              proposal.status === "draft"
                ? "statusDraft"
                : proposal.status === "applied"
                  ? "statusApplied"
                  : "statusRejected",
            )}
          </Badge>
          <span className="text-muted-foreground text-sm">
            {t(isSkill ? "targetSkill" : "targetInstructions")}
          </span>
          {proposal.applied_version !== null && (
            <span className="text-muted-foreground text-sm">
              {t("appliedVersion", { number: proposal.applied_version.number })}
            </span>
          )}
        </div>
        <LineDiff
          before={proposal.old_text}
          after={proposal.new_text}
          identicalLabel={t("diffIdentical")}
          unchangedLabel={(count) => t("diffUnchanged", { count })}
          tooLongLabel={t("diffTooLong")}
        />
        <p className="text-sm whitespace-pre-wrap">{proposal.rationale}</p>
        {isSkill && (
          <p className="text-sm font-medium">
            {t("ungated")} — {t("ungatedWarning")}
          </p>
        )}
        <div className="space-y-2">
          <p className="text-sm font-medium">{t("evalEvidence")}</p>
          {evidence === null && (
            <p className="text-muted-foreground text-sm">{t("noEvalEvidence")}</p>
          )}
          {evidence !== null && <TryView evaluation={evidence} />}
        </div>
        {proposal.status === "draft" && (
          <div className="flex flex-wrap items-center gap-2">
            {canApply && (
              <Button
                type="button"
                disabled={isSkill ? false : !gatePassed}
                title={isSkill || gatePassed ? undefined : t("gateFailed")}
                onClick={onApply}
              >
                {t("apply")}
              </Button>
            )}
            {canReject && !rejecting && (
              <Button type="button" variant="outline" onClick={() => setRejecting(true)}>
                {t("reject")}
              </Button>
            )}
            {canReject && rejecting && (
              <>
                <div className="min-w-64 flex-1 space-y-1.5">
                  <Label htmlFor="reject-reason">{t("rejectReasonLabel")}</Label>
                  <Input
                    id="reject-reason"
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder={t("rejectReasonPlaceholder")}
                  />
                </div>
                <Button
                  type="button"
                  variant="outline"
                  disabled={reason.trim() === ""}
                  onClick={() => onReject(reason.trim())}
                >
                  {t("reject")}
                </Button>
              </>
            )}
          </div>
        )}
        {proposal.status === "applied" && (
          <p className="text-muted-foreground text-sm">{t("rollbackHint")}</p>
        )}
      </CardContent>
    </Card>
  );
}
