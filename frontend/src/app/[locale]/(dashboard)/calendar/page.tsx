"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { MonthGrid } from "@/components/calendar/month-grid";
import { PageHeader } from "@/components/dashboard/page-header";
import { ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useCalendarEvents } from "@/hooks/use-calendar-events";
import {
  CALENDAR_SOURCES,
  calendarTimeZone,
  gridWindow,
  monthGridDays,
  sourceLabelKey,
  type CalendarEventSource,
} from "@/lib/calendar-events";
import { useAuthStore } from "@/stores";

function shiftMonth(cursor: { year: number; month: number }, delta: number) {
  const date = new Date(Date.UTC(cursor.year, cursor.month + delta, 1));
  return { year: date.getUTCFullYear(), month: date.getUTCMonth() };
}

/**
 * The calendar page: one month grid over the user's meetings, routines and
 * reminders. Read-only — the events route reads live and writes nothing, on
 * either side. Source toggles hide lanes; a lane the backend could not read
 * says so instead of rendering an empty month.
 */
export default function CalendarPage() {
  const t = useTranslations("pages.calendar");
  const locale = useLocale();
  const accountZone = useAuthStore((state) => state.user?.time_zone);
  const timeZone = calendarTimeZone(accountZone);
  const today = new Date();
  const [cursor, setCursor] = useState({ year: today.getFullYear(), month: today.getMonth() });
  const [shown, setShown] = useState<Record<CalendarEventSource, boolean>>({
    google_calendar: true,
    outlook_calendar: true,
    routine: true,
    reminder: true,
  });

  const days = useMemo(() => monthGridDays(cursor.year, cursor.month), [cursor]);
  const { from, to } = useMemo(() => gridWindow(days), [days]);
  const { events, isLoading, error, refetch } = useCalendarEvents({ from, to });

  const monthTitle = useMemo(
    () =>
      new Intl.DateTimeFormat(locale, { month: "long", year: "numeric", timeZone }).format(
        new Date(Date.UTC(cursor.year, cursor.month, 1)),
      ),
    [cursor, locale, timeZone],
  );

  const visibleEvents = useMemo(
    () => (events?.events ?? []).filter((event) => shown[event.source]),
    [events, shown],
  );
  const unavailable = useMemo(
    () => Object.entries(events?.sources ?? {}).filter(([, read]) => read.status === "unavailable"),
    [events],
  );

  return (
    <div className="flex flex-1 flex-col space-y-4">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              type="button"
              onClick={() => setCursor({ year: today.getFullYear(), month: today.getMonth() })}
            >
              {t("thisMonth")}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              type="button"
              aria-label={t("previousMonth")}
              onClick={() => setCursor((current) => shiftMonth(current, -1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              type="button"
              aria-label={t("nextMonth")}
              onClick={() => setCursor((current) => shiftMonth(current, 1))}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        }
      />
      <div className="flex flex-wrap items-center gap-4">
        <span className="text-lg font-semibold">{monthTitle}</span>
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
      ) : visibleEvents.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("emptyMonth")}</p>
      ) : (
        <MonthGrid days={days} events={visibleEvents} timeZone={timeZone} locale={locale} />
      )}
    </div>
  );
}
