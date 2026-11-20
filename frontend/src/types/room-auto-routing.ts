export interface RoomAutoRoutingPolicyRead {
  enabled: boolean;
  revision: number;
  can_edit: boolean;
  router_agent_id: string | null;
  /** The server pins the selected agent version for each policy revision. */
  router_version_id: string | null;
  model_profile_id: string | null;
  quiet_window_ms: number;
  max_responders: number;
}

export interface RoomAutoRoutingPolicyWrite {
  expected_revision: number;
  enabled: boolean;
  router_agent_id: string | null;
  model_profile_id: string | null;
  quiet_window_ms: number;
  max_responders: number;
}

export type RoomAutoRoutingPolicyDraft = Omit<RoomAutoRoutingPolicyWrite, "expected_revision">;

/** A public, bounded summary of one room automatic-routing decision. */
export type RoomAutoRoutingDecisionStatus =
  "pending" | "running" | "selected" | "abstained" | "failed" | "unknown";

export interface RoomAutoRoutingSelectedAgent {
  agent_id: string;
  name: string;
}

export interface RoomAutoRoutingDecisionReceipt {
  /** The group's canonical latest human message, where the decision is accepted. */
  accepted_message_id: string;
  /** All human messages combined into this decision, in chronological order. */
  grouped_message_ids: string[];
  status: RoomAutoRoutingDecisionStatus;
  /** Frozen display snapshot of the router selected in the room policy. */
  router_agent_id: string | null;
  router_name: string | null;
  /** Empty unless status is `selected`. */
  selected_agents: RoomAutoRoutingSelectedAgent[];
  accepted_at: string;
  finished_at: string | null;
}

export interface RoomAutoRoutingDecisionsRead {
  items: RoomAutoRoutingDecisionReceipt[];
}
