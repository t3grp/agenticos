import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityPanel } from "./activity-panel";

/**
 * Latest activity: the newest conversations, what the agents did most
 * recently.
 */

const state = vi.hoisted(() => ({
  canRun: true,
  convLoading: false,
  convError: null as Error | null,
  convEnabled: null as boolean | null,
  conversations: [] as {
    id: string;
    title: string | null;
    updated_at: string | null;
    created_at: string;
  }[],
}));

vi.mock("@/hooks", () => ({
  useRecentConversations: (_limit: number, options?: { enabled?: boolean }) => {
    state.convEnabled = options?.enabled ?? true;
    return {
      conversations: state.conversations,
      isLoading: state.convLoading,
      error: state.convError,
      refetch: vi.fn(),
    };
  },
}));

beforeEach(() => {
  state.canRun = true;
  state.convLoading = false;
  state.convError = null;
  state.convEnabled = null;
  state.conversations = [];
});

function renderPanel() {
  return render(<ActivityPanel canRun={state.canRun} />);
}

describe("ActivityPanel", () => {
  it("shows the newest conversations with when they last moved", () => {
    state.conversations = [
      {
        id: "c1",
        title: "Timesheet check-in",
        updated_at: new Date().toISOString(),
        created_at: "2026-08-01T00:00:00.000Z",
      },
      {
        id: "c2",
        title: null,
        updated_at: null,
        created_at: "2026-08-01T00:00:00.000Z",
      },
    ];

    renderPanel();

    expect(screen.getByRole("heading", { name: "Latest activity" })).toBeInTheDocument();
    expect(screen.getByText("Timesheet check-in")).toBeInTheDocument();
    // An untitled thread falls back to the untitled line, dated from creation.
    expect(screen.getByText("Untitled conversation")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /See all activity/ })).toHaveAttribute("href", "/runs");
  });

  it("asks the conversations read only where the caller may run agents", () => {
    state.canRun = false;

    renderPanel();

    expect(state.convEnabled).toBe(false);
    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
  });

  it("holds a skeleton while the conversations load", () => {
    state.convLoading = true;

    renderPanel();

    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByText("Latest activity")).toBeInTheDocument();
  });

  it("says the request failed when the conversations read failed", () => {
    state.convError = new Error("down");

    renderPanel();

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
    expect(screen.queryByText("No conversations yet")).not.toBeInTheDocument();
  });

  it("says there is nothing yet when there are no conversations", () => {
    renderPanel();

    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
    expect(
      screen.getByText("Start one from an agent card - it will be waiting here."),
    ).toBeInTheDocument();
  });
});
