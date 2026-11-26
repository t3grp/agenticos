import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/stores";
import CalendarPage from "./page";
import { monthGridDays, weekGridDays, type CalendarEventsRead } from "@/lib/calendar-events";

vi.mock("@/hooks/use-calendar-events", () => ({
  useCalendarEvents: () => state,
}));

const refetch = vi.fn();
let state: {
  events: CalendarEventsRead | undefined;
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
} = { events: undefined, isLoading: true, error: null, refetch };

function iso(day: number, hour: number): string {
  const today = new Date();
  return new Date(Date.UTC(today.getFullYear(), today.getMonth(), day, hour)).toISOString();
}

const READ: CalendarEventsRead = {
  events: [
    {
      id: "google-1",
      title: "Team standup",
      start: iso(10, 9),
      end: iso(10, 10),
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
      start: iso(15, 14),
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
      id: "google-today",
      title: "Today sync",
      start: iso(new Date().getDate(), 11),
      end: iso(new Date().getDate(), 12),
      source: "google_calendar",
      recurring: false,
      url: null,
      agent_id: null,
      agent_name: null,
      trigger_id: null,
      frequent: false,
    },
  ],
  window_start: iso(1, 0),
  window_end: iso(28, 0),
  sources: {
    google_calendar: { status: "loaded", reason: null, count: 1 },
    outlook_calendar: { status: "unavailable", reason: "the calendar could not be read", count: 0 },
    routine: { status: "loaded", reason: null, count: 1 },
    reminder: { status: "empty", reason: null, count: 0 },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: { time_zone: "UTC" } as never });
  state = { events: READ, isLoading: false, error: null, refetch };
});

describe("the Calendar page", () => {
  it("renders occurrences on the right days", () => {
    const { container } = render(<CalendarPage />);
    const today = new Date();
    const days = monthGridDays(today.getFullYear(), today.getMonth());
    const cells = container.querySelectorAll(".min-h-24");
    expect(cells).toHaveLength(42);
    const standupCell = cells[days.findIndex((day) => day.inMonth && day.day === 10)];
    const routineCell = cells[days.findIndex((day) => day.inMonth && day.day === 15)];
    expect(standupCell?.textContent).toContain("Team standup");
    expect(routineCell?.textContent).toContain("Nightly report");
  });

  it("hides a lane when its source toggle is switched off", () => {
    render(<CalendarPage />);
    expect(screen.getByText(/Team standup/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Google Calendar"));
    expect(screen.queryByText(/Team standup/)).not.toBeInTheDocument();
    expect(screen.getByText(/Nightly report/)).toBeInTheDocument();
  });

  it("says when a source could not be read, instead of an empty month", () => {
    render(<CalendarPage />);
    expect(
      screen.getByText("Outlook is unavailable: the calendar could not be read"),
    ).toBeInTheDocument();
  });

  it("opens the routine's agent page from its popover", async () => {
    render(<CalendarPage />);
    fireEvent.click(screen.getByRole("button", { name: /Nightly report/ }));
    const link = await screen.findByRole("link", { name: "Open link" });
    expect(link).toHaveAttribute("href", "/agents/agent-1");
    expect(screen.getByText("Agent: Reporter")).toBeInTheDocument();
  });

  it("shows one week of seven cells in week view", () => {
    const { container } = render(<CalendarPage />);
    fireEvent.click(screen.getByRole("button", { name: "Week" }));
    const cells = container.querySelectorAll(".min-h-24");
    expect(cells).toHaveLength(7);
    const today = new Date();
    const days = weekGridDays(today.getFullYear(), today.getMonth(), today.getDate());
    const todayCell = cells[days.findIndex((day) => day.day === today.getDate())];
    expect(todayCell?.textContent).toContain("Today sync");
  });

  it("lists one day's events in day view", () => {
    render(<CalendarPage />);
    fireEvent.click(screen.getByRole("button", { name: "Day" }));
    expect(screen.getByText(/Today sync/)).toBeInTheDocument();
    expect(screen.queryByText(/Team standup/)).not.toBeInTheDocument();
  });

  it("shows the empty day state after moving off a day with events", () => {
    render(<CalendarPage />);
    fireEvent.click(screen.getByRole("button", { name: "Day" }));
    fireEvent.click(screen.getByRole("button", { name: "Next day" }));
    expect(screen.getByText("Nothing scheduled on this day.")).toBeInTheDocument();
  });

  it("offers a retry when the window cannot be read", () => {
    state = { events: undefined, isLoading: false, error: new Error("down"), refetch };
    render(<CalendarPage />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });

  it("says so when nothing is scheduled", () => {
    state = {
      events: { ...READ, events: [] },
      isLoading: false,
      error: null,
      refetch,
    };
    render(<CalendarPage />);
    expect(screen.getByText("Nothing scheduled this month.")).toBeInTheDocument();
  });
});
