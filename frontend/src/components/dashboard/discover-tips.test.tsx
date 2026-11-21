import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DiscoverTips } from "./discover-tips";

const data = vi.hoisted(() => ({ perms: new Set<string>() }));

vi.mock("@/hooks", () => ({
  usePermissions: () => ({ can: (perm: string) => data.perms.has(perm) }),
}));

const EVERYTHING = ["agents:edit", "artifacts:view", "channels:manage"];

beforeEach(() => {
  window.localStorage.clear();
  data.perms = new Set(EVERYTHING);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the dashboard's discover tips", () => {
  it("shows one tip at a time, with a way to it", () => {
    render(<DiscoverTips />);

    expect(screen.getByText("Give an agent its own computer")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Turn it on" })).toHaveAttribute("href", "/agents");
    expect(screen.queryByText("Discover AgenticOS")).not.toBeInTheDocument();
  });

  it("leaves out what the reader cannot use", () => {
    data.perms = new Set();
    render(<DiscoverTips />);

    // What needs nothing: the chat's two keys, and connecting one's own client.
    // The first of those is the tip on screen.
    expect(screen.getByText("Point the agent with @")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Try it in chat" })).toHaveAttribute("href", "/chat");
  });

  it("shows the next tip when one is closed, and remembers it", async () => {
    const { unmount } = render(<DiscoverTips />);

    await userEvent.click(
      screen.getByRole("button", { name: "Hide the tip Give an agent its own computer" }),
    );
    expect(screen.queryByText("Give an agent its own computer")).toBeNull();
    expect(screen.getByText("Let agents work as a team")).toBeInTheDocument();

    unmount();
    render(<DiscoverTips />);
    expect(screen.queryByText("Give an agent its own computer")).toBeNull();
    expect(screen.getByText("Let agents work as a team")).toBeInTheDocument();
  });

  it("goes away once every tip is closed", async () => {
    render(<DiscoverTips />);

    for (let count = 0; count < 11; count++) {
      await userEvent.click(screen.getByRole("button", { name: /^Hide the tip / }));
    }

    expect(screen.queryByText(/Give an agent/)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Hide the tip / })).toBeNull();
  });

  it("still closes where the browser keeps nothing", async () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    render(<DiscoverTips />);

    await userEvent.click(
      screen.getByRole("button", { name: "Hide the tip Give an agent its own computer" }),
    );

    expect(screen.queryByText("Give an agent its own computer")).toBeNull();
  });

  it("ignores a stored value that is not a list of tips", () => {
    window.localStorage.setItem("agenticos:discover-tips:dismissed", JSON.stringify({ apps: 1 }));
    render(<DiscoverTips />);

    expect(screen.getByText("Give an agent its own computer")).toBeInTheDocument();
  });
});
