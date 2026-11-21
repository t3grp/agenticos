"use client";

import { useLocale, useTranslations } from "next-intl";

import { formatDateTime } from "@/lib/utils";
import type { RunTranscriptMessage, RunTranscriptRoomReaction } from "@/types/runs";

interface RunRoomReactionAuditProps {
  messages: RunTranscriptMessage[];
  reactions: RunTranscriptRoomReaction[];
  truncated: boolean;
}

/**
 * The run's immutable room-reaction ledger, kept separate from replayable chat
 * messages and provider tool calls. A reaction-only run can have no assistant
 * turn to render, but its audited action is still visible here.
 */
export function RunRoomReactionAudit({
  messages,
  reactions,
  truncated,
}: RunRoomReactionAuditProps) {
  const t = useTranslations("pages.runs");
  const locale = useLocale();

  if (reactions.length === 0 && !truncated) return null;

  return (
    <section
      aria-label={t("roomReactionAudit")}
      className="border-border bg-muted/20 mb-4 space-y-3 rounded-lg border p-3"
    >
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">{t("roomReactionAudit")}</h3>
        <span className="text-muted-foreground text-xs">{reactions.length}</span>
      </div>
      {truncated && (
        <p role="note" className="text-muted-foreground text-xs">
          {t("roomReactionAuditTruncated")}
        </p>
      )}
      {reactions.length > 0 && (
        <ol className="space-y-2">
          {reactions.map((reaction) => {
            const target = messages.find((message) => message.id === reaction.message_id);
            const excerpt = target?.content.replace(/\s+/g, " ").trim();
            const targetPreview =
              excerpt && excerpt.length > 180 ? excerpt.slice(0, 180) + "…" : excerpt;

            return (
              <li
                key={[
                  reaction.message_id,
                  reaction.agent_id,
                  reaction.emoji,
                  reaction.created_at,
                  reaction.tool_call_id ?? "",
                  reaction.decision_id ?? "",
                ].join(":")}
                className="border-border space-y-2 rounded-md border p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {t("roomReactionByAgent", { name: reaction.agent_name })}
                  </span>
                  <span
                    role="img"
                    aria-label={t("roomReactionEmojiLabel", { emoji: reaction.emoji })}
                  >
                    {reaction.emoji}
                  </span>
                  {reaction.reaction_only && (
                    <span className="text-muted-foreground rounded-full border px-2 py-0.5 text-xs">
                      {t("roomReactionOnly")}
                    </span>
                  )}
                </div>

                <div className="space-y-1">
                  <p className="text-muted-foreground text-xs">{t("roomReactionTarget")}</p>
                  <p className="text-sm">{targetPreview ?? t("roomReactionTargetUnavailable")}</p>
                  <code className="text-muted-foreground block text-xs break-all">
                    {reaction.message_id}
                  </code>
                </div>

                <dl className="flex flex-wrap gap-x-4 gap-y-2 text-xs">
                  <div>
                    <dt className="text-muted-foreground">{t("roomReactionAgentId")}</dt>
                    <dd className="font-mono break-all">{reaction.agent_id}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">{t("roomReactionAgentVersionId")}</dt>
                    <dd className="font-mono break-all">{reaction.agent_version_id}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">{t("roomReactionSource")}</dt>
                    <dd>
                      {t(
                        reaction.source === "tool"
                          ? "roomReactionSourceTool"
                          : "roomReactionSourceAutomatic",
                      )}
                    </dd>
                  </div>
                  {reaction.tool_call_id && (
                    <div>
                      <dt className="text-muted-foreground">{t("roomReactionToolCallId")}</dt>
                      <dd className="font-mono break-all">{reaction.tool_call_id}</dd>
                    </div>
                  )}
                  {reaction.decision_id && (
                    <div>
                      <dt className="text-muted-foreground">{t("roomReactionDecisionId")}</dt>
                      <dd className="font-mono break-all">{reaction.decision_id}</dd>
                    </div>
                  )}
                  <div>
                    <dt className="text-muted-foreground">{t("roomReactionCreatedAt")}</dt>
                    <dd>{formatDateTime(reaction.created_at, locale)}</dd>
                  </div>
                </dl>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
