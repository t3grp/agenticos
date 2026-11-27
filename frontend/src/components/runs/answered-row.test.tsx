import { act, render, renderHook, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import messages from "../../../messages/en.json";
import { ANSWERED_FOLD_MS, ANSWERED_HOLD_MS, AnsweredRow, useAnswered } from "./answered-row";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("an answered decision", () => {
  it("says what was decided, folds, then asks to be removed", () => {
    const onGone = vi.fn();
    render(
      <NextIntlClientProvider locale="en" messages={messages}>
        <ul>
          <AnsweredRow item={{ id: "a", title: "Send it", approved: true }} onGone={onGone} />
        </ul>
      </NextIntlClientProvider>,
    );
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Send it");

    act(() => vi.advanceTimersByTime(ANSWERED_HOLD_MS));
    expect(status.closest("[data-open]")).toHaveAttribute("data-open", "false");
    expect(onGone).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(ANSWERED_FOLD_MS));
    expect(onGone).toHaveBeenCalledWith("a");
  });

  it("is kept until dismissed", () => {
    const { result } = renderHook(() => useAnswered());
    act(() => result.current.record({ id: "a", title: "One", approved: true }));
    act(() => result.current.record({ id: "b", title: "Two", approved: false }));
    act(() => result.current.dismiss("a"));
    expect(result.current.answered.map((item) => item.id)).toEqual(["b"]);
  });
});
