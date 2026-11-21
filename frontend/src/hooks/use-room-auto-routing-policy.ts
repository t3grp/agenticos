"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api-error";
import { getRoomAutoRoutingPolicy, putRoomAutoRoutingPolicy } from "@/lib/room-auto-routing-api";
import { announceRoomChange, onRoomChange } from "@/lib/room-events";
import { qk } from "@/lib/query-keys";
import { useConversationStore } from "@/stores";
import type {
  RoomAutoRoutingPolicyDraft,
  RoomAutoRoutingPolicyRead,
  RoomAutoRoutingPolicyWrite,
} from "@/types/room-auto-routing";
import { useRoomIdentity } from "./use-room";

interface SaveState {
  scopeKey: string;
  requestId: symbol;
  error?: unknown;
  conflict?: boolean;
}

export interface RoomAutoRoutingPolicyController {
  policy: RoomAutoRoutingPolicyRead | undefined;
  scopeKey: string;
  isLoading: boolean;
  error: unknown;
  refetch: () => Promise<RoomAutoRoutingPolicyRead | undefined>;
  savePolicy: (draft: RoomAutoRoutingPolicyDraft) => Promise<RoomAutoRoutingPolicyRead | null>;
  isSaving: boolean;
  saveError: unknown;
  isConflict: boolean;
  isCurrent: () => boolean;
}

/** A room policy read and write scoped to its account, organization, room, and selection. */
export function useRoomAutoRoutingPolicy(
  conversationId: string | null,
): RoomAutoRoutingPolicyController {
  const identity = useRoomIdentity();
  const { accountId, organizationId, tenant, current } = identity;
  const currentConversationId = useConversationStore((state) => state.currentConversationId);
  const selectionVersion = useConversationStore((state) => state.selectionVersion);
  const conversationApi = useConversationStore.useApi();
  const queryClient = useQueryClient();
  const scopeKey = JSON.stringify([
    accountId,
    organizationId,
    tenant,
    conversationId,
    selectionVersion,
  ]);
  const queryKey = useMemo(
    () =>
      [
        ...qk.roomRouting.all(accountId, tenant, conversationId),
        organizationId,
        "automatic-policy",
        selectionVersion,
      ] as const,
    [accountId, conversationId, organizationId, selectionVersion, tenant],
  );
  const queryPrefix = useMemo(
    () =>
      [
        ...qk.roomRouting.all(accountId, tenant, conversationId),
        organizationId,
        "automatic-policy",
      ] as const,
    [accountId, conversationId, organizationId, tenant],
  );
  const [saveState, setSaveState] = useState<SaveState | null>(null);
  const isCurrent = useCallback(
    () =>
      conversationId !== null &&
      current() &&
      conversationApi.getState().currentConversationId === conversationId &&
      conversationApi.getState().selectionVersion === selectionVersion,
    [conversationApi, conversationId, current, selectionVersion],
  );
  const invalidate = useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryPrefix }),
    [queryClient, queryPrefix],
  );
  const query = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      if (!isCurrent() || conversationId === null)
        throw new Error("ROOM_AUTO_ROUTING_SCOPE_CHANGED");
      const policy = await getRoomAutoRoutingPolicy(conversationId, signal);
      if (!isCurrent()) throw new Error("ROOM_AUTO_ROUTING_SCOPE_CHANGED");
      return policy;
    },
    enabled:
      conversationId !== null &&
      currentConversationId === conversationId &&
      accountId !== undefined &&
      tenant !== null,
    refetchOnMount: "always",
    retry: false,
  });
  const { refetch } = query;
  const policy =
    query.isFetchedAfterMount && query.isSuccess && isCurrent() ? query.data : undefined;
  const savePolicy = useCallback(
    async (draft: RoomAutoRoutingPolicyDraft): Promise<RoomAutoRoutingPolicyRead | null> => {
      if (!isCurrent() || conversationId === null || !policy?.can_edit) return null;
      const requestId = Symbol("room-auto-routing-save");
      setSaveState({ scopeKey, requestId });
      const input: RoomAutoRoutingPolicyWrite = {
        ...draft,
        expected_revision: policy.revision,
      };
      try {
        const saved = await putRoomAutoRoutingPolicy(conversationId, input);
        if (!isCurrent()) return null;
        queryClient.setQueryData(queryKey, saved);
        announceRoomChange({ conversationId, accountId, organizationId });
        return saved;
      } catch (error) {
        if (!isCurrent()) return null;
        setSaveState({
          scopeKey,
          requestId,
          error,
          conflict: error instanceof ApiError && error.status === 409,
        });
        throw error;
      } finally {
        if (isCurrent()) {
          setSaveState((held) =>
            held?.requestId === requestId && held.error === undefined ? null : held,
          );
        }
      }
    },
    [accountId, conversationId, isCurrent, organizationId, policy, queryClient, queryKey, scopeKey],
  );
  const refresh = useCallback(async () => {
    if (!isCurrent()) return undefined;
    const result = await refetch();
    if (isCurrent() && result.isSuccess) {
      setSaveState((held) => (held?.scopeKey === scopeKey ? null : held));
    }
    return result.data;
  }, [isCurrent, refetch, scopeKey]);

  useEffect(
    () =>
      onRoomChange((event) => {
        if (
          isCurrent() &&
          event.accountId === accountId &&
          event.organizationId === organizationId &&
          event.conversationId === conversationId
        ) {
          void invalidate();
        }
      }),
    [accountId, conversationId, invalidate, isCurrent, organizationId],
  );

  const scopedSaveState = saveState?.scopeKey === scopeKey ? saveState : null;
  return {
    policy,
    scopeKey,
    isLoading: query.isPending,
    error: isCurrent() ? query.error : undefined,
    refetch: refresh,
    savePolicy,
    isSaving: scopedSaveState !== null && scopedSaveState.error === undefined,
    saveError: scopedSaveState?.error,
    isConflict: scopedSaveState?.conflict === true,
    isCurrent,
  };
}
