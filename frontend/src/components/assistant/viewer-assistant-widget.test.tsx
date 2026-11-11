import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ViewerAssistantWidget, viewerChatAgentId } from "./viewer-assistant-widget";

const state = vi.hoisted(() => ({
  pathname: "/apps/app-1",
  artifact: { agent_id: "publisher" as string | null },
  agents: [] as { id: string; status: string; can_run: boolean }[],
  assistantDefault: "missing",
}));

vi.mock("@/lib/locale-navigation", () => ({ usePathname: () => state.pathname }));
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "app-1" }) }));
vi.mock("@/hooks/use-artifacts", () => ({
  useArtifact: () => ({ artifact: state.artifact, isLoading: false }),
}));
vi.mock("@/hooks/use-agents", () => ({ useAgents: () => ({ agents: state.agents, isLoading: false }) }));
vi.mock("./assistant-widget", () => ({
  AssistantWidget: ({ defaultAgentId }: { defaultAgentId?: string | null }) => (
    <div data-testid="assistant" data-default-agent={defaultAgentId ?? state.assistantDefault} />
  ),
}));

describe("viewer assistant launcher", () => {
  beforeEach(() => {
    state.pathname = "/apps/app-1";
    state.artifact = { agent_id: "publisher" };
    state.agents = [{ id: "publisher", status: "published", can_run: true }];
  });

  it("starts with the publishing agent when it is runnable", () => {
    render(<ViewerAssistantWidget />);
    expect(screen.getByTestId("assistant")).toHaveAttribute("data-default-agent", "publisher");
  });

  it.each([
    { reason: "unpublished", agent: { id: "publisher", status: "draft", can_run: true } },
    { reason: "not runnable", agent: { id: "publisher", status: "published", can_run: false } },
  ])("falls back to the organization assistant when the publisher is $reason", ({ agent }) => {
    state.agents = [agent];
    render(<ViewerAssistantWidget />);
    expect(screen.getByTestId("assistant")).toHaveAttribute("data-default-agent", "missing");
  });

  it("does not look up an app for another viewer page", () => {
    state.pathname = "/oauth/consent";
    render(<ViewerAssistantWidget />);
    expect(screen.getByTestId("assistant")).toHaveAttribute("data-default-agent", "missing");
  });
});

describe("viewerChatAgentId", () => {
  it("requires a published agent that the current member can run", () => {
    expect(viewerChatAgentId("publisher", [{ id: "publisher", status: "published", can_run: true }], "assistant"))
      .toBe("publisher");
    expect(viewerChatAgentId("publisher", [{ id: "publisher", status: "published", can_run: false }], "assistant"))
      .toBe("assistant");
    expect(viewerChatAgentId(null, [], "assistant")).toBe("assistant");
  });
});
