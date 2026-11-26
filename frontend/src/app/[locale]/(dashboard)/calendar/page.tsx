"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { DayList } from "@/components/calendar/day-list";
import { MonthGrid } from "@/components/calendar/month-grid";
import { PageHeader } from "@/components/dashboard/page-header";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useCalendarEvents } from "@/hooks/use-calendar-events";
import {
  CALENDAR_SOURCES,
  calendarTimeZone,
  dayKey,
  eventsByDay,
  gridWindow,
  monthGridDays,
  sourceLabelKey,
  weekGridDays,
  type CalendarDay,
  type CalendarEventSource,
} from "@/lib/calendar-events";
import { useAuthStore } from "@/stores";

type CalendarView = "month" | "week" | "day";

function shiftCursor(
  cursor: { year: number; month: number; day: number },
  view: CalendarView,
  delta: number,
): { year: number; month: number; day: number } {
  // The month grid reads year and month only, so a month step pins the day
  // to the 1st rather than letting the 31st drift into the month after next.
  const date =
    view === "month"
      ? new Date(Date.UTC(cursor.year, cursor.month + delta, 1))
      : new Date(
          Date.UTC(cursor.year, cursor.month, cursor.day + (view === "week" ? delta * 7 : delta)),
        );
  return { year: date.getUTCFullYear(), month: date.getUTCMonth(), day: date.getUTCDate() };
}

/**
 * The calendar page: a month, week or day view over the user's meetings,
 * routines and reminders. Read-only — the events route reads live and writes
 * nothing, on either side. Source toggles hide lanes; a lane the backend
 * could not read says so instead of rendering an empty month. The week view
 * is the month grid with one row; the day view is a time-ordered list.
 */
export default function CalendarPage() {
  const t = useTranslations("pages.calendar");
  const locale = useLocale();
  const accountZone = useAuthStore((state) => state.user?.time_zone);
  const timeZone = calendarTimeZone(accountZone);
  const today = new Date();
  const [view, setView] = useState<CalendarView>("month");
  const [cursor, setCursor] = useState({
    year: today.getFullYear(),
    month: today.getMonth(),
    day: today.getDate(),
  });
  const [shown, setShown] = useState<Record<CalendarEventSource, boolean>>({
    google_calendar: true,
    outlook_calendar: true,
    routine: true,
    reminder: true,
  });

  const days: readonly CalendarDay[] = useMemo(
    () =>
      view === "month"
        ? monthGridDays(cursor.year, cursor.month)
        : view === "week"
          ? weekGridDays(cursor.year, cursor.month, cursor.day)
          : [{ year: cursor.year, month: cursor.month, day: cursor.day, inMonth: true }],
    [view, cursor],
  );
  const { from, to } = useMemo(() => gridWindow(days), [days]);
  const { events, isLoading, error, refetch } = useCalendarEvents({ from, to });

  const title = useMemo(() => {
    if (view === "month") {
      return new Intl.DateTimeFormat(locale, { month: "long", year: "numeric", timeZone }).format(
        new Date(Date.UTC(cursor.year, cursor.month, 1)),
      );
    }
    if (view === "day") {
      return new Intl.DateTimeFormat(locale, {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric",
        timeZone,
      }).format(new Date(Date.UTC(cursor.year, cursor.month, cursor.day)));
    }
    const start = days[0];
    const end = days[days.length - 1];
    if (!start || !end) return "";
    const startDate = new Date(Date.UTC(start.year, start.month, start.day));
    const endDate = new Date(Date.UTC(end.year, end.month, end.day));
    const sameYear = start.year === end.year;
    const startText = new Intl.DateTimeFormat(locale, {
      day: "numeric",
      month: "short",
      ...(sameYear ? {} : { year: "numeric" }),
      timeZone,
    }).format(startDate);
    const endText = new Intl.DateTimeFormat(locale, {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone,
    }).format(endDate);
    return `${startText} – ${endText}`;
  }, [view, cursor, days, locale, timeZone]);

  const visibleEvents = useMemo(
    () => (events?.events ?? []).filter((event) => shown[event.source]),
    [events, shown],
  );
  const dayEvents = useMemo(() => {
    if (view !== "day") return [];
    const day = days[0];
    if (!day) return [];
    return eventsByDay(visibleEvents, timeZone).get(dayKey(day)) ?? [];
  }, [view, visibleEvents, timeZone, days]);
  // The day view is empty when its day has nothing, even when the window
  // around it is full; the month and week views are empty only when the
  // whole window is.
  const hasContent = view === "day" ? dayEvents.length > 0 : visibleEvents.length > 0;
  const unavailable = useMemo(
    () => Object.entries(events?.sources ?? {}).filter(([, read]) => read.status === "unavailable"),
    [events],
  );
  const prevLabel =
    view === "month" ? t("previousMonth") : view === "week" ? t("previousWeek") : t("previousDay");
  const nextLabel =
    view === "month" ? t("nextMonth") : view === "week" ? t("nextWeek") : t("nextDay");

  return (
    <div className="flex flex-1 flex-col space-y-4">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex items-center gap-1">
            <div className="mr-2 flex items-center gap-1" role="group" aria-label={t("views")}>
              {(
                [
                  ["month", t("viewMonth")],
                  ["week", t("viewWeek")],
                  ["day", t("viewDay")],
                ] as const
              ).map(([key, label]) => (
                <Button
                  key={key}
                  variant={view === key ? "secondary" : "ghost"}
                  size="sm"
                  type="button"
                  aria-pressed={view === key}
                  onClick={() => setView(key)}
                >
                  {label}
                </Button>
              ))}
            </div>
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={() =>
                setCursor({
                  year: today.getFullYear(),
                  month: today.getMonth(),
                  day: today.getDate(),
                })
              }
            >
              {t("today")}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              type="button"
              aria-label={prevLabel}
              onClick={() => setCursor((current) => shiftCursor(current, view, -1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              type="button"
              aria-label={nextLabel}
              onClick={() => setCursor((current) => shiftCursor(current, view, 1))}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        }
      />
      <div className="flex flex-wrap items-center gap-4">
        <span className="text-lg font-semibold">{title}</span>
        <fieldset className="flex flex-wrap items-center gap-3">
          <legend className="sr-only">{t("sources")}</legend>
          {CALENDAR_SOURCES.map((source) => (
            <label key={source} className="flex cursor-pointer items-center gap-1.5 text-sm">
              <Checkbox
                checked={shown[source]}
                onCheckedChange={(checked) =>
                  setShown((current) => ({ ...current, [source]: checked === true }))
                }
              />
              {t(sourceLabelKey(source))}
            </label>
          ))}
        </fieldset>
      </div>
      {unavailable.map(([source, read]) => (
        <p key={source} role="status" className="text-muted-foreground text-sm">
          {t("sourceUnavailable", {
            source: t(sourceLabelKey(source as CalendarEventSource)),
            reason: read.reason ?? "",
          })}
        </p>
      ))}
      {isLoading ? (
        <LoadingState variant="pending" />
      ) : error ? (
        <ErrorState cta={{ label: t("retry"), onClick: () => void refetch() }} />
      ) : !hasContent ? (
        <p className="text-muted-foreground text-sm">
          {view === "day" ? t("emptyDay") : t("emptyMonth")}
        </p>
      ) : (
        <div data-tour="calendar-month">
          {view === "day" ? (
            <DayList events={dayEvents} timeZone={timeZone} locale={locale} />
          ) : (
            <MonthGrid days={days} events={visibleEvents} timeZone={timeZone} locale={locale} />
          )}
        </div>
      )}
    </div>
  );
}
