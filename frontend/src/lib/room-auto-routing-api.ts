import { apiClient } from "@/lib/api-client";
import type {
  RoomAutoRoutingDecisionsRead,
  RoomAutoRoutingPolicyRead,
  RoomAutoRoutingPolicyWrite,
} from "@/types/room-auto-routing";

const policyPath = (conversationId: string) =>
  `/conversations/${conversationId}/automatic-routing-policy`;

export function getRoomAutoRoutingPolicy(conversationId: string, signal?: AbortSignal) {
  return apiClient.get<RoomAutoRoutingPolicyRead>(policyPath(conversationId), { signal });
}

export function putRoomAutoRoutingPolicy(
  conversationId: string,
  input: RoomAutoRoutingPolicyWrite,
) {
  return apiClient.put<RoomAutoRoutingPolicyRead>(policyPath(conversationId), input);
}

const decisionsPath = (conversationId: string) =>
  `/conversations/${conversationId}/automatic-routing-decisions`;

/** Read receipts for one already-paginated room window, using repeated query keys. */
export function getRoomAutoRoutingDecisions(
  conversationId: string,
  messageIds: readonly string[],
  signal?: AbortSignal,
) {
  const ids = [...new Set(messageIds)].sort();
  if (ids.length === 0 || ids.length > 100 || ids.some((id) => id.length === 0)) {
    throw new RangeError("Room automatic-routing reads require 1–100 message IDs.");
  }

  const params: [string, string][] = ids.map((id) => ["message_id", id]);
  return apiClient.get<RoomAutoRoutingDecisionsRead>(decisionsPath(conversationId), {
    params,
    signal,
  });
}
