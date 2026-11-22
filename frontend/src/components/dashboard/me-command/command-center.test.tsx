import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CommandCenter } from "./command-center";
import type { SectionDef } from "@/lib/dashboard/layouts";
import type { Period } from "@/lib/dashboard/period";
import { Perm, type Permission } from "@/types/permissions";

/**
 * The command center: a status strip, the controls, then the four panels -
 * each rendering only when its section survived the page's gate and filter
 * and its own data is something the caller may read.
 */

const period: Period = { preset: "30d", from: "2026-07-07", to: "2026-08-05" };

vi.mock("./status-strip", () => ({ StatusStrip: () => <div data-testid="status-strip" /> }));
vi.mock("./command-actions", () => ({
  CommandActions: () => <div data-testid="command-actions" />,
}));
vi.mock("./needs-panel", () => ({ NeedsPanel: () => <div data-testid="needs-panel" /> }));
vi.mock("./activity-panel", () => ({
  ActivityPanel: () => <div data-testid="activity-panel" />,
}));
vi.mock("./apps-panel", () => ({ AppsPanel: () => <div data-testid="apps-panel" /> }));
vi.mock("./spend-panel", () => ({ SpendPanel: () => <div data-testid="spend-panel" /> }));

function sections(ids: string[]): SectionDef[] {
  return ids.map((id) => ({ id, titleKey: id, entries: [] }));
}

function holds(...held: Permission[]) {
  return (permission: Permission) => held.includes(permission);
}

function renderCenter(sectionIds: string[], can: (permission: Permission) => boolean) {
  return render(
    <CommandCenter sections={sections(sectionIds)} period={period} can={can} isAppAdmin={false} />,
  );
}

describe("CommandCenter", () => {
  it("shows the strip, the controls and all four panels to a caller who may read them all", () => {
    renderCenter(["needs", "activity", "apps", "spend"], () => true);

    expect(screen.getByTestId("status-strip")).toBeInTheDocument();
    expect(screen.getByTestId("command-actions")).toBeInTheDocument();
    expect(screen.getByTestId("needs-panel")).toBeInTheDocument();
    expect(screen.getByTestId("activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("apps-panel")).toBeInTheDocument();
    expect(screen.getByTestId("spend-panel")).toBeInTheDocument();
  });

  it("a section the filter hid takes its panel with it", () => {
    renderCenter(["spend"], () => true);

    expect(screen.getByTestId("status-strip")).toBeInTheDocument();
    expect(screen.queryByTestId("needs-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("activity-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("apps-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("spend-panel")).toBeInTheDocument();
  });

  it("a viewer keeps Your apps, on the shared shelf alone, and no strip", () => {
    renderCenter(
      ["needs", "activity", "apps", "spend"],
      holds(Perm.agentsView, Perm.collectionsView),
    );

    expect(screen.queryByTestId("status-strip")).not.toBeInTheDocument();
    expect(screen.queryByTestId("needs-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("activity-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("apps-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("spend-panel")).not.toBeInTheDocument();
  });

  it("a caller who runs agents keeps every panel, apps on the most-run list alone", () => {
    renderCenter(["needs", "activity", "apps", "spend"], holds(Perm.agentsRun));

    expect(screen.getByTestId("status-strip")).toBeInTheDocument();
    expect(screen.getByTestId("needs-panel")).toBeInTheDocument();
    expect(screen.getByTestId("activity-panel")).toBeInTheDocument();
    expect(screen.getByTestId("apps-panel")).toBeInTheDocument();
    expect(screen.getByTestId("spend-panel")).toBeInTheDocument();
  });

  it("a caller who may only view agents keeps Your apps, on the shared shelf alone", () => {
    renderCenter(["needs", "activity", "apps", "spend"], holds(Perm.agentsView));

    expect(screen.queryByTestId("status-strip")).not.toBeInTheDocument();
    expect(screen.queryByTestId("needs-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("activity-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("apps-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("spend-panel")).not.toBeInTheDocument();
  });

  it("a section whose every read is refused takes its panel with it", () => {
    renderCenter(["needs", "activity", "apps", "spend"], holds(Perm.projectsView));

    expect(screen.queryByTestId("status-strip")).not.toBeInTheDocument();
    expect(screen.queryByTestId("needs-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("activity-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("apps-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("spend-panel")).not.toBeInTheDocument();
  });

  it("renders nothing when no panel survives", () => {
    const { container } = renderCenter([], () => true);

    expect(container).toBeEmptyDOMElement();
  });
});
