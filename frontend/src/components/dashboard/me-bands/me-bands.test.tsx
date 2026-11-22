import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MeBands } from "./me-bands";
import type { SectionDef } from "@/lib/dashboard/layouts";
import type { Period } from "@/lib/dashboard/period";
import { Perm, type Permission } from "@/types/permissions";

/**
 * Three cards, not three grids: a band renders only when its section
 * survived the page's gate and filter and its own data is something the
 * caller may read.
 */

const period: Period = { preset: "30d", from: "2026-07-07", to: "2026-08-05" };

vi.mock("./ask-band", () => ({ AskBand: () => <div data-testid="ask-band" /> }));
vi.mock("./do-band", () => ({ DoBand: () => <div data-testid="do-band" /> }));
vi.mock("./track-band", () => ({ TrackBand: () => <div data-testid="track-band" /> }));

function sections(ids: string[]): SectionDef[] {
  return ids.map((id) => ({ id, titleKey: id, entries: [] }));
}

function holds(...held: Permission[]) {
  return (permission: Permission) => held.includes(permission);
}

function renderBands(sectionIds: string[], can: (permission: Permission) => boolean) {
  return render(
    <MeBands sections={sections(sectionIds)} period={period} can={can} isAppAdmin={false} />,
  );
}

describe("MeBands", () => {
  it("shows all three bands to a caller who may read them all", () => {
    renderBands(["ask", "do", "track"], () => true);

    expect(screen.getByText("What would you like to do?")).toBeInTheDocument();
    expect(screen.getByTestId("ask-band")).toBeInTheDocument();
    expect(screen.getByTestId("do-band")).toBeInTheDocument();
    expect(screen.getByTestId("track-band")).toBeInTheDocument();
  });

  it("a section the filter hid takes its band with it", () => {
    renderBands(["track"], () => true);

    expect(screen.queryByTestId("ask-band")).not.toBeInTheDocument();
    expect(screen.queryByTestId("do-band")).not.toBeInTheDocument();
    expect(screen.getByTestId("track-band")).toBeInTheDocument();
  });

  it("a viewer keeps Ask, on the shared shelf alone", () => {
    renderBands(["ask", "do", "track"], holds(Perm.agentsView, Perm.collectionsView));

    expect(screen.getByTestId("ask-band")).toBeInTheDocument();
    expect(screen.queryByTestId("do-band")).not.toBeInTheDocument();
    expect(screen.queryByTestId("track-band")).not.toBeInTheDocument();
  });

  it("Do stands on the attention list where the briefing is refused", () => {
    renderBands(["ask", "do", "track"], holds(Perm.agentsView, Perm.runsView));

    expect(screen.getByTestId("ask-band")).toBeInTheDocument();
    expect(screen.getByTestId("do-band")).toBeInTheDocument();
    expect(screen.queryByTestId("track-band")).not.toBeInTheDocument();
  });

  it("a section whose every read is refused takes its band with it", () => {
    renderBands(["ask", "do", "track"], holds(Perm.projectsView));

    expect(screen.queryByTestId("ask-band")).not.toBeInTheDocument();
    expect(screen.queryByTestId("do-band")).not.toBeInTheDocument();
    expect(screen.queryByTestId("track-band")).not.toBeInTheDocument();
  });

  it("renders nothing when no band survives", () => {
    const { container } = renderBands([], () => true);

    expect(container).toBeEmptyDOMElement();
  });
});
