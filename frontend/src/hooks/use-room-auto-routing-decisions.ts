"use client";

import { useCallback, useEffect, useMemo } from "react";
import { queryOptions, useQueries, useQueryClient } from "@tanstack/react-query";

import { getRoomAutoRoutingDecisions } from "@/lib/room-auto-routing-api";
import { onRoomChange } from "@/lib/room-events";
import { qk } from "@/lib/query-keys";
import { useConversationStore } from "@/stores";
import type { ChatMessage } from "@/types/chat";
import type { RoomAutoRoutingDecisionReceipt } from "@/types/room-auto-routing";
import { useRoomIdentity } from "./use-room";

const MESSAGE_ID_BATCH_SIZE = 100;
const ACTIVE_DECISION_POLL_MS = 2_500;

function batchesOf(ids: string[]): string[][] {
  const batches: string[][] = [];
  for (let index = 0; index < ids.length; index += MESSAGE_ID_BATCH_SIZE) {
    batches.push(ids.slice(index, index + MESSAGE_ID_BATCH_SIZE));
  }
  return batches;
}

function persistedHumanMessageId(message: ChatMessage): string | undefined {
  if (message.role !== "user") return undefined;
  const id = message.persistedId ?? (message.isTemporaryId ? undefined : message.id);
  return id || undefined;
}

/**
 * Read safe routing receipts only for saved human messages in the room transcript.
 * Requests stay within the visible message window and the server rechecks room access.
 */
export function useRoomAutoRoutingDecisions(
  conversationId: string | null,
  isRoom: boolean,
  messages: ChatMessage[],
) {
  const { accountId, organizationId, tenant, current } = useRoomIdentity();
  const currentConversationId = useConversationStore((state) => state.currentConversationId);
  const selectionVersion = useConversationStore((state) => state.selectionVersion);
  const conversationApi = useConversationStore.useApi();
  const queryClient = useQueryClient();

  const messageIds = useMemo(
    () =>
      isRoom
        ? [
            ...new Set(
              messages.flatMap((message) => {
                const id = persistedHumanMessageId(message);
                return id === undefined ? [] : [id];
              }),
            ),
          ].sort()
        : [],
    [isRoom, messages],
  );
  const visibleIds = useMemo(() => new Set(messageIds), [messageIds]);
  const batches = useMemo(() => batchesOf(messageIds), [messageIds]);
  const decisionPrefix = useMemo(
    () => qk.roomRouting.decisionPrefix(accountId, tenant, conversationId, organizationId),
    [accountId, conversationId, organizationId, tenant],
  );
  const isCurrent = useCallback(
    () =>
      conversationId !== null &&
      current() &&
      conversationApi.getState().currentConversationId === conversationId &&
      conversationApi.getState().selectionVersion === selectionVersion,
    [conversationApi, conversationId, current, selectionVersion],
  );

  const queries = useQueries({
    queries: batches.map((batchIds) =>
      queryOptions({
        queryKey: qk.roomRouting.decisions(
          accountId,
          tenant,
          conversationId,
          organizationId,
          selectionVersion,
          batchIds,
        ),
        queryFn: async ({ signal }) => {
          if (!isCurrent() || conversationId === null) {
            throw new Error("ROOM_AUTO_ROUTING_SCOPE_CHANGED");
          }
          const result = await getRoomAutoRoutingDecisions(conversationId, batchIds, signal);
          if (!isCurrent()) throw new Error("ROOM_AUTO_ROUTING_SCOPE_CHANGED");
          return result;
        },
        enabled:
          isRoom &&
          conversationId !== null &&
          currentConversationId === conversationId &&
          accountId !== undefined &&
          tenant !== null,
        retry: false,
        refetchOnMount: "always",
        refetchInterval: (query) =>
          query.state.data?.items.some(
            (item) => item.status === "pending" || item.status === "running",
          )
            ? ACTIVE_DECISION_POLL_MS
            : false,
        refetchIntervalInBackground: false,
      }),
    ),
  });

  useEffect(
    () =>
      onRoomChange((event) => {
        if (
          isCurrent() &&
          event.accountId === accountId &&
          event.organizationId === organizationId &&
          event.conversationId === conversationId
        ) {
          void queryClient.invalidateQueries({ queryKey: decisionPrefix });
        }
      }),
    [accountId, conversationId, decisionPrefix, isCurrent, organizationId, queryClient],
  );

  const receiptsByMessageId = useMemo(() => {
    const receipts = new Map<string, RoomAutoRoutingDecisionReceipt>();
    for (const query of queries) {
      if (!query.isFetchedAfterMount || !query.isSuccess) continue;
      for (const receipt of query.data?.items ?? []) {
        const target = visibleIds.has(receipt.accepted_message_id)
          ? receipt.accepted_message_id
          : receipt.grouped_message_ids.find((id) => visibleIds.has(id));
        if (target !== undefined && !receipts.has(target)) receipts.set(target, receipt);
      }
    }
    return receipts;
  }, [queries, visibleIds]);

  const retry = useCallback(
    () => queryClient.invalidateQueries({ queryKey: decisionPrefix }),
    [decisionPrefix, queryClient],
  );
  const enabled =
    isRoom &&
    conversationId !== null &&
    currentConversationId === conversationId &&
    accountId !== undefined &&
    tenant !== null;

  return {
    receiptsByMessageId:
      enabled && isCurrent()
        ? receiptsByMessageId
        : new Map<string, RoomAutoRoutingDecisionReceipt>(),
    error: enabled && isCurrent() ? queries.find((query) => query.isError)?.error : undefined,
    isLoading: enabled && isCurrent() && queries.some((query) => query.isLoading),
    retry,
  };
}
