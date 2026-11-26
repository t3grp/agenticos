import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchCalendarEvents } from "@/lib/calendar-api";
import { useCalendarEvents } from "./use-calendar-events";

vi.mock("@/lib/calendar-api", () => ({
  fetchCalendarEvents: vi.fn(),
}));

const mockedFetch = vi.mocked(fetchCalendarEvents);

const READ = {
  events: [],
  window_start: "2026-11-01T00:00:00Z",
  window_end: "2026-11-30T23:59:59Z",
  sources: {},
};

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedFetch.mockResolvedValue(READ);
});

describe("useCalendarEvents", () => {
  it("reads the window through the client", async () => {
    const { result } = renderHook(
      () => useCalendarEvents({ from: "2026-11-01T00:00:00Z", to: "2026-11-30T23:59:59Z" }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.events).toEqual(READ));
    expect(mockedFetch).toHaveBeenCalledWith({
      from: "2026-11-01T00:00:00Z",
      to: "2026-11-30T23:59:59Z",
      providers: undefined,
    });
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("stays idle while disabled", () => {
    const { result } = renderHook(
      () =>
        useCalendarEvents({
          from: "2026-11-01T00:00:00Z",
          to: "2026-11-30T23:59:59Z",
          enabled: false,
        }),
      { wrapper },
    );
    expect(mockedFetch).not.toHaveBeenCalled();
    expect(result.current.events).toBeUndefined();
    expect(result.current.isLoading).toBe(false);
  });
});
