export interface AgentPin {
  agent_id: string;
  version_id: string;
}
export interface LeadPolicy {
  revision: number;
  enabled: boolean;
  lead: AgentPin | null;
  specialists: AgentPin[];
  max_items: number;
  can_edit: boolean;
}
export interface HandoffItem {
  item_id: string;
  title: string;
  expected_result: string;
  specialist: AgentPin;
  accountable_user_id: string;
  reviewer_user_id: string;
  due_at: string | null;
  existing_task_id: string | null;
  expected_task_revision: number | null;
}
export interface RoutingProposal {
  id: string;
  source_submission_id: string;
  thread_root_message_id: string | null;
  requester_id: string;
  revision: number;
  policy_revision: number;
  status: "pending" | "accepted" | "discarded";
  items: HandoffItem[];
  can_decide: boolean;
  can_accept: boolean;
}
export interface RoomTaskCard {
  reviewer_user_id: string;
  task_id: string;
  proposal_id: string;
  item_id: string;
  thread_root_message_id: string | null;
  title: string;
  status: "planned" | "in_progress" | "waiting_for_user" | "blocked" | "done" | "cancelled";
  next_actor: "user" | "agent";
  assignee_user_id: string | null;
  assignee_agent_id: string | null;
  revision: number;
}
export interface RoomRouting {
  policy: LeadPolicy;
  proposals: RoutingProposal[];
  tasks: RoomTaskCard[];
}
export interface RoutingDecision {
  expected_revision: number;
  idempotency_key: string;
  item_ids: string[];
}
