"use client";

import { EventDetailPopover } from "@/components/calendar/event-detail-popover";
import type { CalendarEvent } from "@/lib/calendar-events";

/**
 * One day's events as a time-ordered list, each row opening its detail
 * popover. The day view is a list rather than a one-cell grid: a grid cell
 * shows three events and counts the rest, which is the wrong shape for a day
 * with a full schedule.
 */
export function DayList({
  events,
  timeZone,
  locale,
}: {
  events: readonly CalendarEvent[];
  timeZone: string;
  locale: string;
}) {
  return (
    <div className="divide-border divide-y rounded-lg border">
      {events.map((event) => (
        <div key={event.id} className="px-2 py-1">
          <EventDetailPopover event={event} timeZone={timeZone} locale={locale} />
        </div>
      ))}
    </div>
  );
}
