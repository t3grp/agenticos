import { apiClient } from "./api-client";
import type {
  LeadPolicy,
  RoomRouting,
  RoutingDecision,
  RoutingProposal,
} from "@/types/room-routing";

const path = (room: string) => `/conversations/${encodeURIComponent(room)}`;
export function readRoomRouting(room: string, root: string | null): Promise<RoomRouting> {
  const query = root === null ? "" : `?thread_root_message_id=${encodeURIComponent(root)}`;
  return apiClient.get(`${path(room)}/routing${query}`);
}
export function saveLeadPolicy(
  room: string,
  policy: Omit<LeadPolicy, "revision" | "can_edit"> & { expected_revision: number },
): Promise<LeadPolicy> {
  return apiClient.put(`${path(room)}/lead-policy`, policy);
}
export function decideRoomProposal(
  room: string,
  proposal: string,
  decision: RoutingDecision,
  accept: boolean,
): Promise<RoutingProposal> {
  return apiClient.post(
    `${path(room)}/routing-proposals/${encodeURIComponent(proposal)}/${accept ? "accept" : "discard"}`,
    decision,
  );
}
