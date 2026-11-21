import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";

import messages from "../../../messages/en.json";
import { ActivityFeed } from "./activity-feed";
import type { FeedRow } from "@/lib/audit-feed";

function row(overrides: Partial<FeedRow> & { id: string }): FeedRow {
  return {
    sentence: "Architect used send email.",
    createdAt: "2026-11-21T05:00:00Z",
    approvedBy: null,
    costUsd: null,
    agentName: "Architect",
    action: "agent.tool_executed",
    ...overrides,
  };
}

function renderFeed(props: { rows: FeedRow[]; isLoading: boolean; error: Error | null }) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ActivityFeed {...props} />
    </NextIntlClientProvider>,
  );
}

describe("ActivityFeed", () => {
  it("reads one sentence per row, with who approved it and what it cost", () => {
    renderFeed({
      rows: [
        row({
          id: "r1",
          approvedBy: "Ada Lovelace",
          costUsd: "0.42",
        }),
        row({ id: "r2", sentence: "Sherman Tan shared the table with Ada Lovelace." }),
      ],
      isLoading: false,
      error: null,
    });

    expect(screen.getByText("Architect used send email.")).toBeInTheDocument();
    expect(screen.getByText(/Approved by Ada Lovelace/)).toBeInTheDocument();
    expect(screen.getByText(/Run cost \$0\.42/)).toBeInTheDocument();
    expect(
      screen.getByText("Sherman Tan shared the table with Ada Lovelace."),
    ).toBeInTheDocument();
  });

  it("says the feed is empty rather than failing silently", () => {
    renderFeed({ rows: [], isLoading: false, error: null });

    expect(screen.getByText(/Nothing here yet/)).toBeInTheDocument();
  });

  it("says the feed could not be read, rather than drawing an empty one", () => {
    renderFeed({ rows: [], isLoading: false, error: new Error("boom") });

    expect(screen.getByText(/could not be read/)).toBeInTheDocument();
  });
});
