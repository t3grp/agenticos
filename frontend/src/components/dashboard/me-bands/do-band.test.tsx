import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DoBand } from "./do-band";
import type { Period } from "@/lib/dashboard/period";

/**
 * Do as one card: the briefing first, the agents that need attention filling
 * the rows behind it, the inbox the one destination.
 */

const period: Period = { preset: "30d", from: "2026-07-07", to: "2026-08-05" };

const state = vi.hoisted(() => ({
  canRun: true,
  canViewRuns: true,
  suggLoading: false,
  suggError: null as Error | null,
  suggestions: [] as {
    id: string;
    title: string;
    detail: string | null;
    urgent?: boolean;
  }[],
  attentionLoading: false,
  attentionError: null as Error | null,
  agents: [] as {
    agent_id: string;
    name: string;
    can_edit: boolean;
    rated_down: number;
    failed_runs: number;
    failing_suites: number;
  }[],
  org: null as { proactive_enabled?: boolean; proactive_watches_enabled?: boolean } | null,
  suggEnabled: null as boolean | null,
  attentionEnabled: null as boolean | null,
}));

vi.mock("@/hooks", () => ({
  useAgentAttention: (_period: unknown, options?: { enabled?: boolean }) => {
    state.attentionEnabled = options?.enabled ?? true;
    return {
      agents: state.agents,
      isLoading: state.attentionLoading,
      error: state.attentionError,
      refetch: vi.fn(),
    };
  },
  useOrganizations: () => ({ activeOrg: state.org }),
}));

vi.mock("@/hooks/use-suggestions", () => ({
  useSuggestions: (enabled = true) => {
    state.suggEnabled = enabled;
    return {
      suggestions: state.suggestions,
      isLoading: state.suggLoading,
      error: state.suggError,
      refetch: vi.fn(),
    };
  },
}));

beforeEach(() => {
  state.canRun = true;
  state.canViewRuns = true;
  state.suggLoading = false;
  state.suggError = null;
  state.suggestions = [];
  state.attentionLoading = false;
  state.attentionError = null;
  state.agents = [];
  state.org = { proactive_enabled: true };
  state.suggEnabled = null;
  state.attentionEnabled = null;
});

function renderBand() {
  return render(<DoBand period={period} canRun={state.canRun} canViewRuns={state.canViewRuns} />);
}

describe("DoBand", () => {
  it("waits for the briefing instead of reporting nothing to do", () => {
    state.suggLoading = true;
    state.attentionLoading = true;
    renderBand();
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByText("Nothing proposed yet")).not.toBeInTheDocument();
  });

  it("is one link to the inbox, leading with what cannot wait", () => {
    state.suggestions = [
      { id: "s1", title: "Approve the agent draft update", detail: "Architect" },
      { id: "s2", title: "Plan your week", detail: null, urgent: true },
      { id: "s3", title: "Review the new routine", detail: "Nightly" },
      { id: "s4", title: "A fourth proposal", detail: null },
    ];
    renderBand();

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/inbox");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    // Urgent first, three rows, the fourth left to the inbox.
    const titles = screen.getAllByText(
      /Approve the agent draft update|Plan your week|Review the new routine/,
    );
    expect(titles).toHaveLength(3);
    expect(
      screen
        .getByText("Plan your week")
        .compareDocumentPosition(screen.getByText("Approve the agent draft update")),
    ).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(screen.queryByText("A fourth proposal")).not.toBeInTheDocument();
    expect(screen.getByText("Architect")).toBeInTheDocument();
  });

  it("fills the rows behind the briefing with the agents that need attention", () => {
    state.suggestions = [{ id: "s1", title: "Approve the agent draft update", detail: null }];
    state.agents = [
      {
        agent_id: "a1",
        name: "Support triage",
        can_edit: true,
        rated_down: 2,
        failed_runs: 1,
        failing_suites: 0,
      },
      {
        agent_id: "a2",
        name: "Front desk",
        can_edit: false,
        rated_down: 0,
        failed_runs: 0,
        failing_suites: 1,
      },
    ];
    renderBand();

    expect(screen.getByText("Approve the agent draft update")).toBeInTheDocument();
    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.getByText("2 answers rated down · 1 failed run")).toBeInTheDocument();
    expect(screen.getByText("Front desk")).toBeInTheDocument();
    expect(screen.getByText("1 failing suite")).toBeInTheDocument();
  });

  it("an agent with nothing to say still gets a row, without an empty subline", () => {
    state.suggestions = [{ id: "s1", title: "Approve the agent draft update", detail: null }];
    state.agents = [
      {
        agent_id: "a1",
        name: "Quiet one",
        can_edit: false,
        rated_down: 0,
        failed_runs: 0,
        failing_suites: 0,
      },
    ];
    renderBand();

    expect(screen.getByText("Quiet one")).toBeInTheDocument();
    expect(screen.queryByText(/rated down|failed run|failing suite/)).not.toBeInTheDocument();
  });

  it("a failed briefing still shows the agents that did answer", () => {
    state.suggError = new Error("offline");
    state.agents = [
      {
        agent_id: "a1",
        name: "Support triage",
        can_edit: false,
        rated_down: 1,
        failed_runs: 0,
        failing_suites: 0,
      },
    ];
    renderBand();

    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.queryByText("Couldn't load this")).not.toBeInTheDocument();
  });

  it("says both sources failed, rather than showing an empty card", () => {
    state.suggError = new Error("offline");
    state.attentionError = new Error("offline");
    renderBand();

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
    expect(screen.queryByText("Nothing proposed yet")).not.toBeInTheDocument();
  });

  it("says there is nothing to do, and what will arrive", () => {
    renderBand();

    expect(screen.getByText("Nothing proposed yet")).toBeInTheDocument();
    expect(
      screen.getByText("Findings from your watches and the nightly suggestions appear here."),
    ).toBeInTheDocument();
  });

  it("says the organization has it off, when it does", () => {
    state.org = null;
    renderBand();

    expect(screen.getByText("Your organization has proactivity off.")).toBeInTheDocument();
  });

  it("asks only the questions its caller may answer", () => {
    state.canRun = false;
    state.canViewRuns = false;
    renderBand();

    expect(state.suggEnabled).toBe(false);
    expect(state.attentionEnabled).toBe(false);
  });
});
