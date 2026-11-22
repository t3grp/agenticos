import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CommandActions } from "./command-actions";
import { Perm, type Permission } from "@/types/permissions";

/**
 * The controls: what a person can start from here, as buttons - each gated
 * on the permission its destination asks.
 */

function holds(...held: Permission[]) {
  return (permission: Permission) => held.includes(permission);
}

describe("CommandActions", () => {
  it("shows every control to a caller who may start them all", () => {
    render(
      <CommandActions can={holds(Perm.agentsRun, Perm.workflowsView, Perm.connectionsView)} />,
    );

    expect(screen.getByRole("link", { name: "New chat" })).toHaveAttribute("href", "/chat");
    expect(screen.getByRole("link", { name: "Build an app" })).toHaveAttribute("href", "/chat");
    expect(screen.getByRole("link", { name: "Start a workflow" })).toHaveAttribute(
      "href",
      "/workflows",
    );
    expect(screen.getByRole("link", { name: "Connect a service" })).toHaveAttribute(
      "href",
      "/connectors",
    );
  });

  it("shows only the workflow control to a caller who may only view workflows", () => {
    render(<CommandActions can={holds(Perm.workflowsView)} />);

    expect(screen.queryByRole("link", { name: "New chat" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Build an app" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start a workflow" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Connect a service" })).not.toBeInTheDocument();
  });

  it("shows only the connect control to a caller who may only view connections", () => {
    render(<CommandActions can={holds(Perm.connectionsView)} />);

    expect(screen.queryByRole("link", { name: "New chat" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a service" })).toBeInTheDocument();
  });

  it("renders nothing for a caller who may start nothing", () => {
    const { container } = render(<CommandActions can={() => false} />);

    expect(container).toBeEmptyDOMElement();
  });
});
