import { describe, expect, it } from "vitest";

import { buildFeedRows, feedAgentNames } from "./audit-feed";
import type { AuditEntry } from "@/types/audit";

function entry(overrides: Partial<AuditEntry> & { id: string }): AuditEntry {
  return {
    actor_user_id: "u-1",
    impersonator_user_id: null,
    action: "agent.tool_executed",
    target_type: "agent_run",
    target_id: "r-1",
    details: null,
    created_at: "2026-11-21T05:00:00Z",
    actor_name: "Sherman Tan",
    sentence: "Architect used send email.",
    ...overrides,
  };
}

describe("buildFeedRows", () => {
  it("joins an executed call to the approval that authorised it", () => {
    const rows = buildFeedRows([
      entry({
        id: "e1",
        details: {
          agent_name: "Architect",
          tool_name: "send_email",
          approval_id: "a-1",
          approved: true,
          cost_usd: "0.42",
        },
      }),
      entry({
        id: "e2",
        action: "approval.approved",
        actor_name: "Ada Lovelace",
        target_type: "tool_approval",
        target_id: "a-1",
        sentence: "Ada Lovelace approved a send email call.",
      }),
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      id: "e1",
      sentence: "Architect used send email.",
      approvedBy: "Ada Lovelace",
      costUsd: "0.42",
      agentName: "Architect",
    });
  });

  it("leaves an unapproved call without an approver, and keeps the row", () => {
    const rows = buildFeedRows([
      entry({
        id: "e1",
        details: { agent_name: "Architect", tool_name: "web_search", approved: false },
      }),
    ]);

    expect(rows).toEqual([
      {
        id: "e1",
        sentence: "Architect used send email.",
        createdAt: "2026-11-21T05:00:00Z",
        approvedBy: null,
        costUsd: null,
        agentName: "Architect",
        action: "agent.tool_executed",
      },
    ]);
  });

  it("keeps a decision on its own where no executed call consumed it", () => {
    const rows = buildFeedRows([
      entry({
        id: "e1",
        action: "approval.rejected",
        actor_name: "Ada Lovelace",
        target_type: "tool_approval",
        target_id: "a-9",
        sentence: "Ada Lovelace rejected a delete row call.",
      }),
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0].sentence).toBe("Ada Lovelace rejected a delete row call.");
    expect(rows[0].approvedBy).toBeNull();
  });

  it("does not invent an approver for a decision on another page", () => {
    const rows = buildFeedRows([
      entry({
        id: "e1",
        details: { agent_name: "Architect", approval_id: "a-7", approved: true },
      }),
    ]);

    expect(rows).toHaveLength(1);
    expect(rows[0].approvedBy).toBeNull();
  });

  it("falls back to the action code where the read path rendered no sentence", () => {
    const rows = buildFeedRows([entry({ id: "e1", sentence: null })]);

    expect(rows[0].sentence).toBe("agent.tool_executed");
  });
});

describe("feedAgentNames", () => {
  it("lists each agent once, in first-seen order", () => {
    const rows = buildFeedRows([
      entry({ id: "e1", details: { agent_name: "Architect" } }),
      entry({ id: "e2", details: { agent_name: "Timesheet" } }),
      entry({ id: "e3", details: { agent_name: "Architect" } }),
      entry({ id: "e4", details: {} }),
    ]);

    expect(feedAgentNames(rows)).toEqual(["Architect", "Timesheet"]);
  });
});
