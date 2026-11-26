import { describe, expect, it } from "vitest";

import {
  calendarTimeZone,
  dayKey,
  daysInMonth,
  eventDayKey,
  eventsByDay,
  formatEventSpan,
  formatEventTime,
  gridWeekdayKeys,
  gridWindow,
  monthGridDays,
  sourceDotClass,
  sourceLabelKey,
  type CalendarEvent,
} from "./calendar-events";

function event(partial: Partial<CalendarEvent> & { start: string }): CalendarEvent {
  return {
    id: "e1",
    title: "Standup",
    end: null,
    source: "google_calendar",
    recurring: false,
    url: null,
    agent_id: null,
    agent_name: null,
    trigger_id: null,
    frequent: false,
    ...partial,
  };
}

describe("daysInMonth", () => {
  it("counts February in a leap year and a common year", () => {
    expect(daysInMonth(2024, 1)).toBe(29);
    expect(daysInMonth(2025, 1)).toBe(28);
    expect(daysInMonth(2026, 10)).toBe(30);
  });
});

describe("monthGridDays", () => {
  it("lays November 2026 out Monday-first, six weeks, with the neighbours marked", () => {
    // 1 November 2026 is a Sunday, so the grid opens on Monday 26 October.
    const days = monthGridDays(2026, 10);
    expect(days).toHaveLength(42);
    expect(days[0]).toEqual({ year: 2026, month: 9, day: 26, inMonth: false });
    expect(days[6]).toEqual({ year: 2026, month: 10, day: 1, inMonth: true });
    expect(days[35]).toEqual({ year: 2026, month: 10, day: 30, inMonth: true });
    expect(days[41]).toEqual({ year: 2026, month: 11, day: 6, inMonth: false });
  });

  it("starts on the 1st when the month opens on a Monday", () => {
    const days = monthGridDays(2026, 5);
    expect(days[0]).toEqual({ year: 2026, month: 5, day: 1, inMonth: true });
  });
});

describe("gridWeekdayKeys", () => {
  it("heads the grid Monday-first", () => {
    expect(gridWeekdayKeys()).toEqual([
      "weekdayMon",
      "weekdayTue",
      "weekdayWed",
      "weekdayThu",
      "weekdayFri",
      "weekdaySat",
      "weekdaySun",
    ]);
  });
});

describe("dayKey", () => {
  it("pads month and day", () => {
    expect(dayKey({ year: 2026, month: 0, day: 5, inMonth: true })).toBe("2026-01-05");
    expect(dayKey({ year: 2026, month: 10, day: 30, inMonth: true })).toBe("2026-11-30");
  });
});

describe("eventDayKey", () => {
  it("reads the day in the display zone, not the browser's", () => {
    // 00:30 in Singapore is still the previous day in UTC.
    expect(eventDayKey("2026-11-03T00:30:00+08:00", "Asia/Singapore")).toBe("2026-11-03");
    expect(eventDayKey("2026-11-03T00:30:00+08:00", "UTC")).toBe("2026-11-02");
  });
});

describe("eventsByDay", () => {
  it("groups by day in the display zone and orders each day by start", () => {
    const late = event({ id: "late", start: "2026-11-03T14:00:00+08:00" });
    const early = event({ id: "early", start: "2026-11-03T09:00:00+08:00" });
    const otherDay = event({ id: "other", start: "2026-11-04T09:00:00+08:00" });
    const byDay = eventsByDay([late, early, otherDay], "Asia/Singapore");
    expect([...byDay.keys()]).toEqual(["2026-11-03", "2026-11-04"]);
    expect(byDay.get("2026-11-03")?.map((item) => item.id)).toEqual(["early", "late"]);
  });
});

describe("gridWindow", () => {
  it("covers the grid with a day of buffer either side", () => {
    const { from, to } = gridWindow(monthGridDays(2026, 10));
    expect(from).toBe("2026-10-25T00:00:00.000Z");
    expect(to).toBe("2026-12-07T23:59:59.999Z");
  });

  it("refuses an empty grid", () => {
    expect(() => gridWindow([])).toThrow("gridWindow needs a non-empty grid");
  });
});

describe("calendarTimeZone", () => {
  it("keeps the account's zone, and falls back to the browser's", () => {
    expect(calendarTimeZone("Asia/Singapore")).toBe("Asia/Singapore");
    expect(calendarTimeZone(null)).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });
});

describe("formatEventTime and formatEventSpan", () => {
  it("renders 09:00 for a Singapore reader and 01:00 UTC for a UTC reader", () => {
    const start = "2026-11-03T09:00:00+08:00";
    expect(formatEventTime(start, "Asia/Singapore", "en-GB")).toBe("09:00");
    expect(formatEventTime(start, "UTC", "en-GB")).toBe("01:00");
  });

  it("spans a start and an end, or the start alone", () => {
    const timed = event({
      start: "2026-11-03T09:00:00+08:00",
      end: "2026-11-03T10:00:00+08:00",
    });
    expect(formatEventSpan(timed, "Asia/Singapore", "en-GB")).toBe("09:00 – 10:00");
    const open = event({ start: "2026-11-03T09:00:00+08:00" });
    expect(formatEventSpan(open, "Asia/Singapore", "en-GB")).toBe("09:00");
  });
});

describe("sourceLabelKey and sourceDotClass", () => {
  it("names a key and a colour for every source", () => {
    expect(sourceLabelKey("google_calendar")).toBe("sourceGoogleCalendar");
    expect(sourceLabelKey("outlook_calendar")).toBe("sourceOutlookCalendar");
    expect(sourceLabelKey("routine")).toBe("sourceRoutine");
    expect(sourceLabelKey("reminder")).toBe("sourceReminder");
    expect(sourceDotClass("google_calendar")).toBe("bg-sky-500");
    expect(sourceDotClass("outlook_calendar")).toBe("bg-indigo-500");
    expect(sourceDotClass("routine")).toBe("bg-emerald-500");
    expect(sourceDotClass("reminder")).toBe("bg-amber-500");
  });
});
