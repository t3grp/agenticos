import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageSquare } from "lucide-react";

import { BandCard, BandNote, BandRow } from "./band-card";

/** The card is the click: one link, its rows a preview, its foot naming where it goes. */

describe("BandCard", () => {
  it("renders as one link to its destination, with its rows inside it", () => {
    render(
      <BandCard
        href="/chat"
        icon={MessageSquare}
        title="Ask"
        description="Your conversations, ready to continue."
        go="Open chat"
      >
        <BandRow title="Q3 pipeline summary" subline="2 hours ago" />
        <BandRow title="Architect" />
      </BandCard>,
    );

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/chat");
    expect(link).toHaveTextContent("Ask");
    expect(link).toHaveTextContent("Open chat");
    expect(screen.getByText("Your conversations, ready to continue.")).toBeInTheDocument();
    expect(screen.getByText("Q3 pipeline summary")).toBeInTheDocument();
    expect(screen.getByText("2 hours ago")).toBeInTheDocument();
    expect(screen.getByText("Architect")).toBeInTheDocument();
    // The rows are a preview, not links of their own.
    expect(screen.getAllByRole("link")).toHaveLength(1);
  });

  it("a row without a subline renders its title alone", () => {
    render(
      <BandCard
        href="/chat"
        icon={MessageSquare}
        title="Ask"
        description="Your conversations, ready to continue."
        go="Open chat"
      >
        <BandRow title="Architect" />
      </BandCard>,
    );

    expect(screen.getByText("Architect")).toBeInTheDocument();
  });

  it("a note says why a band has nothing to preview", () => {
    render(
      <BandCard
        href="/chat"
        icon={MessageSquare}
        title="Ask"
        description="Your conversations, ready to continue."
        go="Open chat"
      >
        <BandNote title="No conversations yet" description="Start one from an agent card." />
      </BandCard>,
    );

    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
    expect(screen.getByText("Start one from an agent card.")).toBeInTheDocument();
  });
});
