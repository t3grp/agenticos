import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SpendPanel } from "./spend-panel";
import type { Period } from "@/lib/dashboard/period";

/**
 * Spend: what the work cost, without saying the strip's figures twice - the
 * cost per run, how many finished, and the agent that ran most.
 */

const period: Period = { preset: "30d", from: "2026-07-07", to: "2026-08-05" };

const state = vi.hoisted(() => ({
  usageLoading: false,
  usageError: null as Error | null,
  usageMissing: false,
  totalRuns: 0,
  costUsd: "0",
  byAgent: [] as { agent_id: string; name: string; runs: number }[],
  byStatus: [] as { status: string; runs: number }[],
}));

vi.mock("@/hooks", () => ({
  useUsageStats: () => ({
    usage: state.usageMissing
      ? undefined
      : {
          total_runs: state.totalRuns,
          cost: { period_usd: state.costUsd },
          by_agent: state.byAgent,
          by_status: state.byStatus,
        },
    isLoading: state.usageLoading,
    error: state.usageError,
    refetch: vi.fn(),
  }),
}));

beforeEach(() => {
  state.usageLoading = false;
  state.usageError = null;
  state.usageMissing = false;
  state.totalRuns = 0;
  state.costUsd = "0";
  state.byAgent = [];
  state.byStatus = [];
});

function renderPanel() {
  return render(<SpendPanel period={period} />);
}

describe("SpendPanel", () => {
  it("shows the cost per run, the finished share and the busiest agent", () => {
    state.totalRuns = 40;
    state.costUsd = "1.64";
    state.byAgent = [{ agent_id: "a1", name: "Support triage", runs: 40 }];
    state.byStatus = [
      { status: "completed", runs: 36 },
      { status: "failed", runs: 2 },
      { status: "budget_exceeded", runs: 1 },
      { status: "awaiting_approval", runs: 1 },
    ];

    renderPanel();

    expect(screen.getByRole("heading", { name: "Spend" })).toBeInTheDocument();
    expect(screen.getByText("What the work cost.")).toBeInTheDocument();
    // $1.64 over 40 runs is about four cents a run.
    expect(screen.getByText("About $0.04 a run")).toBeInTheDocument();
    expect(screen.getByText("Cost per run this period")).toBeInTheDocument();
    expect(screen.getByText("90% finished")).toBeInTheDocument();
    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.getByText("40 runs")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /See spending/ })).toHaveAttribute("href", "/runs");
  });

  it("omits the agent row where the runs carry no agent", () => {
    state.totalRuns = 2;
    state.costUsd = "0.20";
    state.byStatus = [{ status: "completed", runs: 2 }];

    renderPanel();

    expect(screen.getByText("About $0.10 a run")).toBeInTheDocument();
    expect(screen.getByText("100% finished")).toBeInTheDocument();
    expect(screen.queryByText("Support triage")).not.toBeInTheDocument();
  });

  it("holds a skeleton while the usage loads", () => {
    state.usageLoading = true;

    renderPanel();

    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByText("No runs yet")).not.toBeInTheDocument();
  });

  it("says the request failed when the usage read failed", () => {
    state.usageError = new Error("down");

    renderPanel();

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
    expect(screen.queryByText("No runs yet")).not.toBeInTheDocument();
  });

  it("says no runs yet where the window holds none", () => {
    renderPanel();

    expect(screen.getByText("No runs yet")).toBeInTheDocument();
    expect(
      screen.getByText("Runs and spend will show here once your agents have run."),
    ).toBeInTheDocument();
  });

  it("says no runs yet where the usage itself never answered", () => {
    state.usageMissing = true;

    renderPanel();

    expect(screen.getByText("No runs yet")).toBeInTheDocument();
  });
});
