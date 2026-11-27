import { render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginOrb } from "./login-orb";

const orb = (root: HTMLElement) => root.querySelector<HTMLElement>("[data-state]");

afterEach(() => vi.restoreAllMocks());

describe("LoginOrb", () => {
  it("draws nothing on the server", () => {
    expect(renderToString(<LoginOrb />)).not.toContain("data-state");
  });

  it.each([
    [0, "56px", "var(--brand-300)"],
    [0.999, "72px", "var(--brand-600)"],
  ])("draws a still orb with its status in words (%s)", (random, size, accent) => {
    vi.spyOn(Math, "random").mockReturnValue(random);
    const { container } = render(<LoginOrb />);
    expect(orb(container)?.style.width).toBe(size);
    expect(orb(container)?.style.backgroundColor).toBe(accent);
    expect(orb(container)?.dataset.state).toBe("idle");
    expect(screen.getByText("Ready to sign in")).toBeInTheDocument();
  });
});
