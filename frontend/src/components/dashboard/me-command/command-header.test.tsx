import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CommandHeader } from "./command-header";

/**
 * The command center's heading: the day, the name of the page, and the
 * primary action beside it.
 */

describe("CommandHeader", () => {
  it("names the page and renders its actions beside the title", () => {
    render(<CommandHeader actions={<button type="button">New chat</button>} />);

    expect(screen.getByRole("heading", { name: /Command center/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New chat" })).toBeInTheDocument();
  });

  it("renders without actions", () => {
    render(<CommandHeader />);

    expect(screen.getByRole("heading", { name: /Command center/ })).toBeInTheDocument();
  });
});
