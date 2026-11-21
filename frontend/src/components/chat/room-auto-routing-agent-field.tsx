"use client";

import { useTranslations } from "next-intl";
import {
  Button,
  FormField,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import type { RoomParticipantRead } from "@/types/room";

interface Props {
  candidates: RoomParticipantRead[];
  value: string | null;
  disabled: boolean;
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
  onChange: (value: string | null) => void;
}

/** Only opted-in, eligible members of this room can be selected as the router. */
export function RoomAutoRoutingAgentField({
  candidates,
  value,
  disabled,
  loading,
  failed,
  onRetry,
  onChange,
}: Props) {
  const t = useTranslations("chat.roomAutoRouting");
  const tc = useTranslations("common");
  const selected = value ?? "none";
  const available = candidates.some((candidate) => candidate.id === value);

  return (
    <div className="space-y-2">
      <FormField label={t("routerAgent")} htmlFor="room-auto-routing-agent">
        <Select
          value={selected}
          onValueChange={(next) => onChange(next === "none" ? null : next)}
          disabled={disabled || loading || failed}
        >
          <SelectTrigger id="room-auto-routing-agent">
            <SelectValue placeholder={t("chooseRouterAgent")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">{t("chooseRouterAgent")}</SelectItem>
            {!available && value !== null && (
              <SelectItem value={value} disabled>
                {t("routerAgentUnavailable")}
              </SelectItem>
            )}
            {candidates.map((candidate) => (
              <SelectItem key={candidate.id} value={candidate.id}>
                {candidate.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </FormField>
      <p className="text-muted-foreground text-xs">{t("eligibleAgentsHint")}</p>
      <p className="text-muted-foreground text-xs">{t("mentionsPriorityHint")}</p>
      {loading && (
        <p role="status" className="text-muted-foreground text-xs">
          {tc("loading")}
        </p>
      )}
      {failed && (
        <div role="alert" className="flex items-center gap-2 text-xs">
          <span className="text-destructive">{t("rosterUnavailable")}</span>
          <Button type="button" variant="ghost" size="sm" onClick={onRetry} disabled={disabled}>
            {tc("retry")}
          </Button>
        </div>
      )}
      {!loading && !failed && candidates.length === 0 && (
        <p className="text-muted-foreground text-xs">{t("noEligibleAgents")}</p>
      )}
    </div>
  );
}
