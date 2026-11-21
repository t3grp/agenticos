"use client";

import { useTranslations } from "next-intl";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { RoomMessageReactionSummary } from "@/types/room-message-reaction";

interface RoomMessageReactionChipProps {
  reaction: RoomMessageReactionSummary;
  canReact: boolean;
  pending: boolean;
  onToggle: () => void;
}

export function RoomMessageReactionChip({
  reaction,
  canReact,
  pending,
  onToggle,
}: RoomMessageReactionChipProps) {
  const t = useTranslations("chat.roomReactions");
  const agentNames = (reaction.agents ?? []).map((agent) => agent.name);
  const attribution =
    agentNames.length > 0 ? t("reactedByAgents", { names: agentNames.join(", ") }) : undefined;
  const label = attribution
    ? `${reaction.emoji} ${reaction.count} · ${attribution}`
    : `${reaction.emoji} ${reaction.count}`;
  const content = (
    <>
      <span aria-hidden>{reaction.emoji}</span>
      <span className="tabular-nums">{reaction.count}</span>
    </>
  );
  const chip =
    canReact || attribution ? (
      <button
        type="button"
        onClick={canReact ? onToggle : undefined}
        disabled={canReact && pending}
        aria-pressed={canReact ? reaction.reacted_by_me : undefined}
        aria-label={label}
        className={cn(
          "inline-flex h-7 items-center gap-1 rounded-full border px-2 text-xs transition-colors disabled:opacity-60",
          canReact && reaction.reacted_by_me
            ? "border-primary/40 bg-primary/10 text-foreground"
            : "border-foreground/10 bg-muted/40 text-muted-foreground",
          canReact ? "hover:bg-muted" : "cursor-help",
        )}
      >
        {content}
      </button>
    ) : (
      <span
        role="img"
        aria-label={label}
        className="border-foreground/10 bg-muted/40 text-muted-foreground inline-flex h-7 items-center gap-1 rounded-full border px-2 text-xs"
      >
        {content}
      </span>
    );

  if (!attribution) return chip;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{chip}</TooltipTrigger>
      <TooltipContent>{attribution}</TooltipContent>
    </Tooltip>
  );
}
