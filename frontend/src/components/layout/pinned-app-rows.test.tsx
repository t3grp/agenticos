import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PinnedAppRows } from "./pinned-app-rows";

const currentPath = vi.fn<() => string>(() => "/dashboard");
const can = vi.fn<(permission: string) => boolean>(() => true);
const pinnedApps = vi.fn<() => { id: string; title: string }[]>(() => []);

vi.mock("next/navigation", () => ({
  usePathname: () => currentPath(),
  redirect: vi.fn(),
  permanentRedirect: vi.fn(),
}));
vi.mock("@/hooks/use-permissions", () => ({ usePermissions: () => ({ can }) }));
vi.mock("@/hooks/use-artifacts", () => ({
  usePinnedArtifacts: () => ({ artifacts: pinnedApps() }),
}));

function renderRows(props: { collapsed?: boolean; onNavigate?: () => void } = {}) {
  return render(
    <PinnedAppRows collapsed={props.collapsed ?? false} onNavigate={props.onNavigate} />,
  );
}

describe("PinnedAppRows", () => {
  it("renders nothing while the reader has no pins", () => {
    pinnedApps.mockReturnValue([]);
    const { container } = renderRows();
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the pinned apps in pin order, lighting the one open", () => {
    currentPath.mockReturnValue("/apps/a2");
    pinnedApps.mockReturnValue([
      { id: "a1", title: "Weekly report" },
      { id: "a2", title: "Treasury board" },
    ]);
    renderRows();

    const links = screen.getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["/apps/a1", "/apps/a2"]);
    expect(screen.getByRole("link", { name: /Weekly report/ })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: /Treasury board/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByText("W")).toBeInTheDocument();
    pinnedApps.mockReturnValue([]);
    currentPath.mockReturnValue("/dashboard");
  });

  it("runs the caller's callback on navigation, so the drawer can close", () => {
    pinnedApps.mockReturnValue([{ id: "a1", title: "Weekly report" }]);
    const onNavigate = vi.fn();
    renderRows({ onNavigate });
    screen.getByRole("link", { name: /Weekly report/ }).click();
    expect(onNavigate).toHaveBeenCalled();
    pinnedApps.mockReturnValue([]);
  });

  it("draws the rail as tiles with titles, without the words", () => {
    pinnedApps.mockReturnValue([{ id: "a1", title: "Weekly report" }]);
    renderRows({ collapsed: true });

    const link = screen.getByRole("link", { name: "Weekly report" });
    expect(link).toHaveAttribute("title", "Weekly report");
    expect(screen.queryByText("Weekly report")).toBeNull();
    expect(screen.getByText("W")).toBeInTheDocument();
    pinnedApps.mockReturnValue([]);
  });
});
