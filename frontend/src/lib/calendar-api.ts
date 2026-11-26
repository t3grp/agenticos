import { apiClient } from "@/lib/api-client";
import type { CalendarEventsRead } from "@/lib/calendar-events";

/** What the calendar page asks the events route for. */
export interface FetchCalendarEventsParams {
  /** Window start, inclusive, ISO with offset. */
  from: string;
  /** Window end, inclusive, ISO with offset. */
  to: string;
  /** Provider keys to read; both external calendars when omitted. */
  providers?: string[];
}

/**
 * The window's events, routines and reminders on one timeline, with each
 * source's outcome. Goes through the BFF proxy, which forwards the call to
 * the backend's calendar events endpoint.
 */
export function fetchCalendarEvents({
  from,
  to,
  providers,
}: FetchCalendarEventsParams): Promise<CalendarEventsRead> {
  return apiClient.get<CalendarEventsRead>("/calendar/events", {
    params: {
      from,
      to,
      ...(providers ? { providers: providers.join(",") } : {}),
    },
  });
}
