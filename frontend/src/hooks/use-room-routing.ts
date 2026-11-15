"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { readRoomRouting, saveLeadPolicy, decideRoomProposal } from "@/lib/room-routing-api";
import { qk } from "@/lib/query-keys";
import { useRoomIdentity } from "./use-room";
import type { LeadPolicy, RoutingProposal } from "@/types/room-routing";

export function useRoomRouting(room: string | null, root: string | null = null) {
  const identity = useRoomIdentity();
  const cache = useQueryClient();
  const queryKey = qk.roomRouting.list(identity.accountId, identity.tenant, room, root);
  const query = useQuery({
    queryKey,
    queryFn: () => readRoomRouting(room!, root),
    enabled: room !== null && identity.accountId !== undefined && identity.tenant !== null,
    refetchInterval: 5000,
  });
  async function save(
    policy: Omit<LeadPolicy, "revision" | "can_edit"> & { expected_revision: number },
  ) {
    if (room === null || !identity.current()) return;
    await saveLeadPolicy(room, policy);
    if (identity.current())
      await cache.invalidateQueries({
        queryKey: qk.roomRouting.room(identity.accountId, identity.tenant, room),
      });
  }
  async function decide(
    proposal: RoutingProposal,
    accept: boolean,
    key: string,
    itemIds: string[],
  ) {
    if (room === null || !identity.current()) return;
    await decideRoomProposal(
      room,
      proposal.id,
      { expected_revision: proposal.revision, idempotency_key: key, item_ids: itemIds },
      accept,
    );
    if (identity.current()) await cache.invalidateQueries({ queryKey });
  }
  return { ...query, save, decide, isCurrent: identity.current };
}
