import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ORB_ANIMATION_DELAY_MS, StatusOrb } from "./status-orb";

let reports: Array<(visible: boolean) => void> = [];

beforeEach(() => {
  reports = [];
  vi.useFakeTimers();
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: IntersectionObserverCallback) {
        reports.push((isIntersecting) =>
          callback([{ isIntersecting } as IntersectionObserverEntry], this as never),
        );
      }
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const orbs = (root: HTMLElement) => [...root.querySelectorAll<HTMLElement>("[data-state]")];
const moving = (root: HTMLElement) => orbs(root).filter((orb) => orb.dataset.animate);
const showAll = () => act(() => reports.forEach((report) => report(true)));
const wait = () => act(() => void vi.advanceTimersByTime(ORB_ANIMATION_DELAY_MS));

describe("StatusOrb", () => {
  it("is hidden from assistive technology and takes its size", () => {
    const { container } = render(<StatusOrb state="done" size={64} />);
    const [orb] = orbs(container);
    expect(orb).toHaveAttribute("aria-hidden", "true");
    expect(orb).toHaveStyle({ width: "64px", height: "64px" });
  });

  it("moves only after the delay and pauses off screen", () => {
    const { container } = render(<StatusOrb state="working" />);
    showAll();
    expect(moving(container)).toHaveLength(0);
    wait();
    expect(moving(container)).toHaveLength(1);
    act(() => reports[0]!(false));
    expect(moving(container)).toHaveLength(0);
  });

  it("moves three at once and hands a freed slot to the next", () => {
    const { container, unmount } = render(
      <>
        {[1, 2, 3, 4, 5].map((n) => (
          <StatusOrb key={n} state="waiting" />
        ))}
      </>,
    );
    showAll();
    wait();
    expect(moving(container)).toEqual(orbs(container).slice(0, 3));
    // The last one leaves the queue without ever having held a slot.
    act(() => reports[4]!(false));
    act(() => reports[0]!(false));
    expect(moving(container)).toEqual(orbs(container).slice(1, 4));
    unmount();
  });

  it.each(["idle", "done", "failed"] as const)("never moves when %s", (state) => {
    const { container } = render(<StatusOrb state={state} />);
    showAll();
    wait();
    expect(moving(container)).toHaveLength(0);
  });

  it("stays still under reduced motion", () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    const { container } = render(<StatusOrb state="working" />);
    showAll();
    wait();
    expect(moving(container)).toHaveLength(0);
  });
});
