import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppsPanel } from "./apps-panel";
import type { Period } from "@/lib/dashboard/period";

/**
 * Your apps: the agents the caller runs most, or the agents shared with
 * them where they may not run agents at all.
 */

const period: Period = { preset: "30d", from: "2026-07-07", to: "2026-08-05" };

const state = vi.hoisted(() => ({
  canRun: true,
  usageLoading: false,
  usageError: null as Error | null,
  usageMissing: false,
  byAgent: [] as { agent_id: string; name: string; runs: number }[],
  listLoading: false,
  listError: null as Error | null,
  agents: [] as { id: string; name: string; owner_user_id: string | null }[],
  can: (_permission: string) => false as boolean,
  userId: "u1" as string | null,
  usageEnabled: null as boolean | null,
  listEnabled: null as boolean | null,
}));

vi.mock("@/hooks", () => ({
  useUsageStats: (_period: unknown, options?: { enabled?: boolean }) => {
    state.usageEnabled = options?.enabled ?? true;
    return {
      usage: state.usageMissing ? undefined : { by_agent: state.byAgent },
      isLoading: state.usageLoading,
      error: state.usageError,
      refetch: vi.fn(),
    };
  },
  useAgents: (options?: { enabled?: boolean }) => {
    state.listEnabled = options?.enabled ?? true;
    return {
      agents: state.agents,
      isLoading: state.listLoading,
      error: state.listError,
      refetch: vi.fn(),
    };
  },
  usePermissions: () => ({ can: state.can }),
}));

vi.mock("@/stores", () => ({
  useAuthStore: (selector: (value: unknown) => unknown) =>
    selector({ user: state.userId === null ? null : { id: state.userId } }),
}));

beforeEach(() => {
  state.canRun = true;
  state.usageLoading = false;
  state.usageError = null;
  state.usageMissing = false;
  state.byAgent = [];
  state.listLoading = false;
  state.listError = null;
  state.agents = [];
  state.can = () => false;
  state.userId = "u1";
  state.usageEnabled = null;
  state.listEnabled = null;
});

function renderPanel() {
  return render(<AppsPanel period={period} canRun={state.canRun} />);
}

describe("AppsPanel", () => {
  it("shows the most-run agents with their run counts", () => {
    state.byAgent = [
      { agent_id: "a1", name: "Support triage", runs: 40 },
      { agent_id: "a2", name: "Architect", runs: 12 },
    ];

    renderPanel();

    expect(screen.getByRole("heading", { name: "Your apps" })).toBeInTheDocument();
    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.getByText("40 runs")).toBeInTheDocument();
    expect(screen.getByText("Architect")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All apps/ })).toHaveAttribute("href", "/apps");
    expect(state.usageEnabled).toBe(true);
    expect(state.listEnabled).toBe(false);
  });

  it("a viewer sees the agents shared with them, tagged shared", () => {
    state.canRun = false;
    state.agents = [{ id: "a9", name: "Support triage", owner_user_id: "u2" }];

    renderPanel();

    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.getByText("shared")).toBeInTheDocument();
    expect(state.usageEnabled).toBe(false);
    expect(state.listEnabled).toBe(true);
  });

  it("a viewer with no signed-in user still sees the agents shared with them", () => {
    state.canRun = false;
    state.userId = null;
    state.agents = [{ id: "a9", name: "Support triage", owner_user_id: "u2" }];

    renderPanel();

    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.getByText("shared")).toBeInTheDocument();
  });

  it("says no apps have run yet where the caller runs agents but none have", () => {
    renderPanel();

    expect(screen.getByText("No apps run yet")).toBeInTheDocument();
    expect(
      screen.getByText("Ask an agent to build one, or run one you already have."),
    ).toBeInTheDocument();
  });

  it("says nothing is shared yet where a viewer has no shared agents", () => {
    state.canRun = false;

    renderPanel();

    expect(screen.getByText("No agents yet")).toBeInTheDocument();
    expect(
      screen.getByText("Nothing is shared with you yet - ask a builder, or create your own."),
    ).toBeInTheDocument();
  });

  it("holds a skeleton while the usage loads", () => {
    state.usageLoading = true;

    renderPanel();

    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });

  it("holds a skeleton while the shared list loads", () => {
    state.canRun = false;
    state.listLoading = true;

    renderPanel();

    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });

  it("says the request failed when the usage read failed", () => {
    state.usageError = new Error("down");

    renderPanel();

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
    expect(screen.queryByText("No apps run yet")).not.toBeInTheDocument();
  });

  it("says the request failed when the shared list failed", () => {
    state.canRun = false;
    state.listError = new Error("down");

    renderPanel();

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
  });
});
