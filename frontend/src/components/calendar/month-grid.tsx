"use client";

import { useTranslations } from "next-intl";

import { EventDetailPopover } from "@/components/calendar/event-detail-popover";
import {
  dayKey,
  eventsByDay,
  gridWeekdayKeys,
  type CalendarDay,
  type CalendarEvent,
} from "@/lib/calendar-events";
import { cn } from "@/lib/utils";

/** How many events a day cell shows before it counts the rest. */
const VISIBLE_PER_DAY = 3;

/**
 * The month grid: weekday headings, then six weeks of day cells. Days
 * outside the shown month render muted. Each event is a chip that opens its
 * detail popover; a day with more than three events counts the rest.
 */
export function MonthGrid({
  days,
  events,
  timeZone,
  locale,
}: {
  days: readonly CalendarDay[];
  events: readonly CalendarEvent[];
  timeZone: string;
  locale: string;
}) {
  const t = useTranslations("pages.calendar");
  const weekdayT = useTranslations("triggers");
  const byDay = eventsByDay(events, timeZone);
  return (
    <div className="space-y-1">
      <div className="grid grid-cols-7 gap-1">
        {gridWeekdayKeys().map((key) => (
          <div
            key={key}
            className="text-muted-foreground px-2 py-1 text-center text-xs font-medium"
          >
            {weekdayT(key)}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {days.map((day) => {
          const key = dayKey(day);
          const dayEvents = byDay.get(key) ?? [];
          const shown = dayEvents.slice(0, VISIBLE_PER_DAY);
          const extra = dayEvents.length - shown.length;
          return (
            <div
              key={key}
              className={cn(
                "border-border min-h-24 rounded-lg border p-1.5",
                !day.inMonth && "bg-muted/40",
              )}
            >
              <div
                className={cn(
                  "px-1 text-xs font-medium",
                  day.inMonth ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {day.day}
              </div>
              <div className="mt-1 space-y-0.5">
                {shown.map((event) => (
                  <EventDetailPopover
                    key={event.id}
                    event={event}
                    timeZone={timeZone}
                    locale={locale}
                  />
                ))}
                {extra > 0 && (
                  <div className="text-muted-foreground px-1 text-xs">
                    {t("moreEvents", { count: extra })}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
