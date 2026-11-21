import type { AuditEntry } from "@/types/audit";

/** One row of the activity feed: a sentence, when it happened, and the join
 * the plan asks for - who approved it, and what the run cost. */
export interface FeedRow {
  id: string;
  sentence: string;
  createdAt: string | null;
  /** Who approved the action, where the trail names one on this page. */
  approvedBy: string | null;
  /** What the run the action belongs to cost, where the entry carries it. */
  costUsd: string | null;
  /** The agent behind the row, for the agent filter. */
  agentName: string | null;
  action: string;
}

function detailString(details: AuditEntry["details"], key: string): string | null {
  const value = details?.[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Audit entries as feed rows, joining each executed tool call to the approval
 * decision that authorised it.
 *
 * The join is page-scoped: a `agent.tool_executed` entry names its approval in
 * `details.approval_id`, and the `approval.approved` entry it names carries
 * the decider as its actor. A decision on another page - or a call that needed
 * none - leaves `approvedBy` null rather than inventing one, and the decision
 * row is shown on its own only where no executed call consumed it. Rows keep
 * the entries' newest-first order.
 */
export function buildFeedRows(entries: readonly AuditEntry[]): FeedRow[] {
  const decisions = new Map<string, AuditEntry>();
  for (const entry of entries) {
    if (
      entry.action.startsWith("approval.") &&
      entry.target_type === "tool_approval" &&
      entry.target_id !== null
    ) {
      decisions.set(entry.target_id, entry);
    }
  }
  const consumed = new Set<string>();
  for (const entry of entries) {
    if (entry.action !== "agent.tool_executed") continue;
    const approvalId = detailString(entry.details, "approval_id");
    if (approvalId !== null && decisions.has(approvalId)) consumed.add(approvalId);
  }

  const rows: FeedRow[] = [];
  for (const entry of entries) {
    if (
      entry.action.startsWith("approval.") &&
      entry.target_id !== null &&
      consumed.has(entry.target_id)
    ) {
      continue;
    }
    let approvedBy: string | null = null;
    if (entry.action === "agent.tool_executed") {
      const approvalId = detailString(entry.details, "approval_id");
      const decision = approvalId !== null ? decisions.get(approvalId) : undefined;
      if (decision?.action === "approval.approved") {
        approvedBy = decision.actor_name ?? null;
      }
    }
    rows.push({
      id: entry.id,
      sentence: entry.sentence ?? entry.action,
      createdAt: entry.created_at,
      approvedBy,
      costUsd: detailString(entry.details, "cost_usd"),
      agentName: detailString(entry.details, "agent_name"),
      action: entry.action,
    });
  }
  return rows;
}

/** The agents behind a page of rows, for the agent filter, in first-seen order. */
export function feedAgentNames(rows: readonly FeedRow[]): string[] {
  const names: string[] = [];
  for (const row of rows) {
    if (row.agentName !== null && !names.includes(row.agentName)) names.push(row.agentName);
  }
  return names;
}
