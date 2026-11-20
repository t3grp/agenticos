"use client";

import { useTranslations } from "next-intl";

import type {
  RoomAutoRoutingDecisionReceipt,
  RoomAutoRoutingDecisionStatus,
} from "@/types/room-auto-routing";

const STATUS_KEYS: Record<RoomAutoRoutingDecisionStatus, string> = {
  pending: "pending",
  running: "running",
  selected: "selectedBy",
  abstained: "abstained",
  failed: "failed",
  unknown: "unknown",
};

/** Show who made the routing decision and, when dispatched, who was selected. */
export function RoomAutoRoutingReceipt({ receipt }: { receipt: RoomAutoRoutingDecisionReceipt }) {
  const t = useTranslations("chat.roomAutoRouting.receipt");
  const router = receipt.router_name?.trim() || t("router");
  const responders = receipt.selected_agents
    .map((agent) => agent.name.trim())
    .filter((name) => name.length > 0);

  return (
    <div className="text-muted-foreground mt-2 flex flex-wrap items-baseline gap-x-1.5 gap-y-1 pl-11 text-xs">
      <span>{t(STATUS_KEYS[receipt.status], { router })}</span>
      {receipt.status === "selected" && (
        <span className="text-foreground/80 font-medium">
          {responders.length > 0 ? responders.join(", ") : t("selectedNamesUnavailable")}
        </span>
      )}
    </div>
  );
}
