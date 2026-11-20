"use client";

import { useCallback, useMemo } from "react";
import { useAgents } from "@/hooks/use-agents";
import { useRoomParticipants } from "@/hooks/use-room-participants";
import type { RoomParticipantRead } from "@/types/room";

/** Current room members who can run as ordinary published agents. */
export function useRoomAutoRoutingCandidates(conversationId: string | null) {
  const roster = useRoomParticipants(conversationId);
  const agentsQuery = useAgents();
  const candidates = useMemo(() => {
    const available = new Set(
      agentsQuery.agents
        .filter(
          (agent) =>
            agent.status === "published" &&
            agent.can_run &&
            !agent.local_workspace &&
            !agent.local_coding_agent &&
            !agent.personal_coding_harness,
        )
        .map((agent) => agent.id),
    );
    return roster.participants.filter(
      (participant): participant is RoomParticipantRead & { kind: "agent" } =>
        participant.kind === "agent" && participant.is_member && available.has(participant.id),
    );
  }, [agentsQuery.agents, roster.participants]);
  const refetchParticipants = roster.refetch;
  const refetchAgents = agentsQuery.refetch;
  const retry = useCallback(
    () => Promise.all([refetchParticipants(), refetchAgents()]),
    [refetchAgents, refetchParticipants],
  );
  return {
    candidates,
    isLoading: roster.isLoading || agentsQuery.isLoading,
    error: roster.error ?? agentsQuery.error,
    retry,
  };
}
