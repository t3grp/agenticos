import { WEEKDAYS, defaultTimeZone } from "@/lib/trigger-format";

/**
 * The calendar view's read shape, mirroring the backend's
 * `app/schemas/calendar.py`. One event per row, whatever its source: an
 * external meeting, a routine occurrence, or a reminder (a one-shot routine).
 */
export type CalendarEventSource = "google_calendar" | "outlook_calendar" | "routine" | "reminder";

export type CalendarSourceStatus = "loaded" | "empty" | "unavailable";

export interface CalendarEvent {
  id: string;
  title: string;
  /** ISO with offset, as the backend sent it. */
  start: string;
  end: string | null;
  source: CalendarEventSource;
  recurring: boolean;
  url: string | null;
  agent_id: string | null;
  agent_name: string | null;
  trigger_id: string | null;
  /**
   * A routine whose interval is under an hour has no meaningful calendar
   * occurrence, so the backend shows one marker per day instead of hundreds
   * of events. The marker says so; it is never silently hidden.
   */
  frequent: boolean;
}

export interface CalendarSourceRead {
  status: CalendarSourceStatus;
  reason: string | null;
  count: number;
}

export interface CalendarEventsRead {
  events: CalendarEvent[];
  window_start: string;
  window_end: string;
  sources: Record<string, CalendarSourceRead>;
}

/** The four sources, in the order the page lists them. */
export const CALENDAR_SOURCES: readonly CalendarEventSource[] = [
  "google_calendar",
  "outlook_calendar",
  "routine",
  "reminder",
];

/**
 * The catalog key holding one source's label, under `pages.calendar`. A module
 * table holds keys, and the component translates at the point of use.
 */
const SOURCE_LABEL_KEYS: Record<CalendarEventSource, string> = {
  google_calendar: "sourceGoogleCalendar",
  outlook_calendar: "sourceOutlookCalendar",
  routine: "sourceRoutine",
  reminder: "sourceReminder",
};

export function sourceLabelKey(source: CalendarEventSource): string {
  return SOURCE_LABEL_KEYS[source];
}

/** The dot colour one source's chips carry, in both themes. */
const SOURCE_DOT_CLASSES: Record<CalendarEventSource, string> = {
  google_calendar: "bg-sky-500",
  outlook_calendar: "bg-indigo-500",
  routine: "bg-emerald-500",
  reminder: "bg-amber-500",
};

export function sourceDotClass(source: CalendarEventSource): string {
  return SOURCE_DOT_CLASSES[source];
}

/** One day on the month grid, as year/month/day in the display zone. */
export interface CalendarDay {
  year: number;
  month: number;
  /** 1-based. */
  day: number;
  /** Whether the day belongs to the month being shown. */
  inMonth: boolean;
}

/** Days in a month, leap years included. */
export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
}

/**
 * The month grid: six weeks of seven days, Monday-first, starting with the
 * Monday of (or before) the 1st. Days outside the month are still returned,
 * marked `inMonth: false`, so the grid never changes shape.
 */
export function monthGridDays(year: number, month: number): CalendarDay[] {
  const first = new Date(Date.UTC(year, month, 1));
  // getUTCDay: 0 = Sunday. Monday-first means the 1st sits (day + 6) % 7
  // cells into the first week.
  const lead = (first.getUTCDay() + 6) % 7;
  const days: CalendarDay[] = [];
  for (let cell = 0; cell < 42; cell += 1) {
    const date = new Date(Date.UTC(year, month, 1 - lead + cell));
    days.push({
      year: date.getUTCFullYear(),
      month: date.getUTCMonth(),
      day: date.getUTCDate(),
      inMonth: date.getUTCMonth() === month,
    });
  }
  return days;
}

/** The weekday headings above the grid, Monday-first, as in `WEEKDAYS`. */
export function gridWeekdayKeys(): string[] {
  return WEEKDAYS.map((day) => day.key);
}

/** The `YYYY-MM-DD` key one day renders under. */
export function dayKey(day: CalendarDay): string {
  const month = String(day.month + 1).padStart(2, "0");
  const date = String(day.day).padStart(2, "0");
  return `${day.year}-${month}-${date}`;
}

/**
 * The `YYYY-MM-DD` key one event's start falls on, read in the display zone
 * rather than the browser's — an account zone ahead of UTC moves a late
 * meeting onto the next day, and the grid must agree with the clock it shows.
 */
export function eventDayKey(startIso: string, timeZone: string): string {
  // en-CA formats a date as YYYY-MM-DD, which is the key itself.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(startIso));
}

/** Events grouped by the day they fall on, each day's events in start order. */
export function eventsByDay(
  events: readonly CalendarEvent[],
  timeZone: string,
): Map<string, CalendarEvent[]> {
  const byDay = new Map<string, CalendarEvent[]>();
  for (const event of events) {
    const key = eventDayKey(event.start, timeZone);
    const dayEvents = byDay.get(key);
    if (dayEvents) {
      dayEvents.push(event);
    } else {
      byDay.set(key, [event]);
    }
  }
  for (const dayEvents of byDay.values()) {
    dayEvents.sort((a, b) => a.start.localeCompare(b.start));
  }
  return byDay;
}

/**
 * The window the grid asks the backend for: the first grid day to the last,
 * with a day of buffer either side so an event just past midnight in the
 * display zone is not clipped by a UTC boundary. Well inside the backend's
 * 90-day cap.
 */
export function gridWindow(days: readonly CalendarDay[]): { from: string; to: string } {
  const first = days[0];
  const last = days[days.length - 1];
  if (!first || !last) {
    throw new Error("gridWindow needs a non-empty grid");
  }
  const from = new Date(Date.UTC(first.year, first.month, first.day - 1)).toISOString();
  const to = new Date(Date.UTC(last.year, last.month, last.day + 1, 23, 59, 59, 999)).toISOString();
  return { from, to };
}

/** The zone times are read in: the account's, else this browser's. */
export function calendarTimeZone(accountZone: string | null | undefined): string {
  return defaultTimeZone(accountZone);
}

/** "09:00" for one event's start, in the display zone. */
export function formatEventTime(startIso: string, timeZone: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(startIso));
}

/**
 * "09:00 – 10:00" for one event's span, or the start alone when it has no
 * end. A frequent marker has no meaningful span; the caller shows its label
 * instead of calling this.
 */
export function formatEventSpan(event: CalendarEvent, timeZone: string, locale: string): string {
  const start = formatEventTime(event.start, timeZone, locale);
  if (!event.end) return start;
  return `${start} – ${formatEventTime(event.end, timeZone, locale)}`;
}
