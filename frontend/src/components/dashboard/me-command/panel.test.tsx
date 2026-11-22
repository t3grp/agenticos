import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CommandPanel, PanelNote, PanelRow } from "./panel";

/**
 * A panel is a heading, a description, preview rows and a footer link to the
 * destination - never a link itself.
 */

describe("CommandPanel", () => {
  it("renders the heading, description, rows and the foot naming the destination", () => {
    render(
      <CommandPanel
        title="Needs you"
        description="Decisions only you can make."
        go="Open inbox"
        href="/inbox"
      >
        <PanelRow title="Approvals" subline="2 pending" />
      </CommandPanel>,
    );

    expect(screen.getByRole("heading", { name: "Needs you" })).toBeInTheDocument();
    expect(screen.getByText("Decisions only you can make.")).toBeInTheDocument();
    expect(screen.getByText("Approvals")).toBeInTheDocument();
    expect(screen.getByText("2 pending")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Open inbox/ });
    expect(link).toHaveAttribute("href", "/inbox");
  });

  it("a row without a subline renders the title alone", () => {
    render(
      <CommandPanel title="Spend" description="What the work cost." go="See spending" href="/runs">
        <PanelRow title="90% finished" />
      </CommandPanel>,
    );

    expect(screen.getByText("90% finished")).toBeInTheDocument();
  });

  it("a note renders its title and description", () => {
    render(
      <CommandPanel
        title="Latest activity"
        description="What your agents did, in plain words."
        go="See all activity"
        href="/runs"
      >
        <PanelNote title="No conversations yet" description="Start one from an agent card." />
      </CommandPanel>,
    );

    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
    expect(screen.getByText("Start one from an agent card.")).toBeInTheDocument();
  });
});
