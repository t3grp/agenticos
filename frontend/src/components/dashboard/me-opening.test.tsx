import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MeOpening } from "./me-opening";

/**
 * The person's dashboard opens Do with Get started, for whoever is still
 * setting up; the Briefing is a widget in the Do band now, not a second half
 * of this opening.
 */

vi.mock("./start-checklist", () => ({
  StartChecklist: ({ orgId, className }: { orgId: string; className?: string }) => (
    <section aria-label={`checklist:${orgId}`} className={className} />
  ),
}));

describe("MeOpening", () => {
  it("shows the start checklist for a person in an organization", () => {
    render(<MeOpening orgId="o1" />);

    expect(screen.getByRole("region", { name: "checklist:o1" })).toHaveClass("mt-0");
  });

  it("shows nothing without an organization", () => {
    render(<MeOpening orgId={null} />);

    expect(screen.queryByRole("region")).toBeNull();
  });

  it("holds a loading state while the start state is still arriving", () => {
    render(<MeOpening orgId="o1" pending />);

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "checklist:o1" })).toBeNull();
  });
});
