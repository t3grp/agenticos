import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { RoomAutoRoutingDecisionReceipt } from "@/types/room-auto-routing";
import { RoomAutoRoutingReceipt } from "./room-auto-routing-receipt";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, string>) => {
    const translated = key === "router" ? "The room router" : key;
    return values?.router ? `${translated}:${values.router}` : translated;
  },
}));

function receipt(
  overrides: Partial<RoomAutoRoutingDecisionReceipt> = {},
): RoomAutoRoutingDecisionReceipt {
  return {
    accepted_message_id: "message-accepted",
    grouped_message_ids: ["message-accepted"],
    status: "selected",
    router_agent_id: "router-id",
    router_name: "Room router",
    selected_agents: [{ agent_id: "agent-a", name: "Support" }],
    accepted_at: "2026-11-20T06:00:00Z",
    finished_at: "2026-11-20T06:00:02Z",
    ...overrides,
  };
}

describe("RoomAutoRoutingReceipt", () => {
  it("shows who selected the responders and their frozen display names", () => {
    render(
      <RoomAutoRoutingReceipt
        receipt={receipt({
          selected_agents: [
            { agent_id: "agent-a", name: "Support" },
            { agent_id: "agent-b", name: "Research" },
            { agent_id: "agent-c", name: "  " },
          ],
        })}
      />,
    );

    expect(screen.getByText("selectedBy:Room router")).toBeInTheDocument();
    expect(screen.getByText("Support, Research")).toBeInTheDocument();
  });

  it.each([
    ["pending", "pending"],
    ["running", "running"],
    ["abstained", "abstained"],
    ["failed", "failed"],
    ["unknown", "unknown"],
  ] as const)(
    "renders the %s receipt status without exposing responder details",
    (status, label) => {
      render(
        <RoomAutoRoutingReceipt
          receipt={receipt({
            status,
            router_name: null,
            selected_agents: [{ agent_id: "agent-a", name: "Private detail" }],
          })}
        />,
      );

      expect(screen.getByText(`${label}:The room router`)).toBeInTheDocument();
      expect(screen.queryByText("Private detail")).not.toBeInTheDocument();
    },
  );

  it("handles a selected decision whose names are absent", () => {
    render(
      <RoomAutoRoutingReceipt
        receipt={receipt({ selected_agents: [{ agent_id: "agent-a", name: "  " }] })}
      />,
    );

    expect(screen.getByText("selectedNamesUnavailable")).toBeInTheDocument();
  });
});
