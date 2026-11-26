import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/lib/api-client";
import { fetchCalendarEvents } from "./calendar-api";

vi.mock("@/lib/api-client", () => ({
  apiClient: { get: vi.fn() },
}));

const mockedGet = vi.mocked(apiClient.get);

beforeEach(() => {
  vi.clearAllMocks();
  mockedGet.mockResolvedValue({ events: [], sources: {} });
});

describe("fetchCalendarEvents", () => {
  it("asks the BFF route for the window, both providers by default", async () => {
    await fetchCalendarEvents({ from: "2026-11-01T00:00:00Z", to: "2026-11-30T23:59:59Z" });
    expect(mockedGet).toHaveBeenCalledWith("/calendar/events", {
      params: { from: "2026-11-01T00:00:00Z", to: "2026-11-30T23:59:59Z" },
    });
  });

  it("passes the chosen providers as one comma-separated value", async () => {
    await fetchCalendarEvents({
      from: "2026-11-01T00:00:00Z",
      to: "2026-11-30T23:59:59Z",
      providers: ["google_calendar", "outlook_calendar"],
    });
    expect(mockedGet).toHaveBeenCalledWith("/calendar/events", {
      params: {
        from: "2026-11-01T00:00:00Z",
        to: "2026-11-30T23:59:59Z",
        providers: "google_calendar,outlook_calendar",
      },
    });
  });
});
