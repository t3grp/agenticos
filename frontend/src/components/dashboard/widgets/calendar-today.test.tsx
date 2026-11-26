import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import messages from "../../../../messages/en.json";
import { TooltipProvider } from "@/components/ui";
import type { Period } from "@/lib/dashboard/period";
import type { CalendarEventsRead } from "@/lib/calendar-events";
import { useAuthStore } from "@/stores";
import { CalendarTodayWidget } from "./calendar-today";

const refetch = vi.fn();
let state: {
  events: CalendarEventsRead | undefined;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
} = { events: undefined, isLoading: true, error: null, refetch };

vi.mock("@/hooks/use-calendar-events", () => ({
  useCalendarEvents: () => state,
}));

const PERIOD: Period = { preset: "30d", from: "2026-07-19", to: "2026-08-18" };

function hoursFromNow(hours: number): string {
  return new Date(Date.now() + hours * 60 * 60 * 1000).toISOString();
}

const READ: CalendarEventsRead = {
  events: [
    {
      id: "google-1",
      title: "Team standup",
      start: hoursFromNow(3),
      end: hoursFromNow(4),
      source: "google_calendar",
      recurring: true,
      url: "https://calendar.google.com/event?eid=abc",
      agent_id: null,
      agent_name: null,
      trigger_id: null,
      frequent: false,
    },
    {
      id: "routine-1",
      title: "Nightly report",
      start: hoursFromNow(1),
      end: null,
      source: "routine",
      recurring: true,
      url: null,
      agent_id: "agent-1",
      agent_name: "Reporter",
      trigger_id: "trigger-1",
      frequent: false,
    },
    {
      id: "routine-frequent",
      title: "Heartbeat check (runs frequently)",
      start: hoursFromNow(2),
      end: null,
      source: "routine",
      recurring: true,
      url: null,
      agent_id: "agent-1",
      agent_name: "Reporter",
      trigger_id: "trigger-2",
      frequent: true,
    },
  ],
  window_start: hoursFromNow(0),
  window_end: hoursFromNow(24),
  sources: {
    google_calendar: { status: "loaded", reason: null, count: 1 },
    outlook_calendar: { status: "empty", reason: null, count: 0 },
    routine: { status: "loaded", reason: null, count: 2 },
    reminder: { status: "empty", reason: null, count: 0 },
  },
};

function renderWidget() {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <TooltipProvider>
        <CalendarTodayWidget
          title="Today"
          hint="Meetings, routines and reminders in the next 24 hours, soonest first."
          period={PERIOD}
          seeAll="/calendar"
        />
      </TooltipProvider>
    </NextIntlClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: { time_zone: "UTC" } as never });
  state = { events: READ, isLoading: false, error: null, refetch };
});

describe("the Today calendar card", () => {
  it("lists the next 24 hours soonest first, with the time and the source", () => {
    const { container } = renderWidget();
    const rows = container.querySelectorAll("li");
    expect(rows).toHaveLength(3);
    // The 1-hour routine comes before the 3-hour meeting, whatever the order
    // the backend sent them in.
    expect(rows[0]?.textContent).toContain("Nightly report");
    expect(rows[1]?.textContent).toContain("Heartbeat check");
    expect(rows[2]?.textContent).toContain("Team standup");
    expect(rows[2]?.textContent).toContain("Google Calendar");
    expect(rows[0]?.textContent).toContain("Routines");
  });

  it("says a frequent routine runs frequently instead of giving it a span", () => {
    renderWidget();
    expect(screen.getByText("Runs frequently")).toBeInTheDocument();
  });

  it("holds its shape while the window loads", () => {
    state = { events: undefined, isLoading: true, error: null, refetch };
    renderWidget();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("Team standup")).not.toBeInTheDocument();
  });

  it("says so when nothing is in the next 24 hours", () => {
    state = { events: { ...READ, events: [] }, isLoading: false, error: null, refetch };
    renderWidget();
    expect(screen.getByText("Nothing in the next 24 hours")).toBeInTheDocument();
  });

  it("offers a retry when the window cannot be read", () => {
    state = { events: undefined, isLoading: false, error: new Error("down"), refetch };
    renderWidget();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });
});
