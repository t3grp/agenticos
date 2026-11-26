"use client";

import { useMemo } from "react";
import { useLocale, useTranslations } from "next-intl";
import { CalendarDays } from "lucide-react";

import { StatusList, type StatusRow } from "../primitives/status-list";
import { WidgetFrame } from "../widget-frame";
import { WidgetEmptyBody, WidgetErrorBody, WidgetSkeleton } from "../widget-states";
import type { DashboardWidgetProps } from "./types";
import { useCalendarEvents } from "@/hooks/use-calendar-events";
import type { Translate } from "@/lib/agent-step-captions";
import {
  calendarTimeZone,
  formatEventSpan,
  sourceLabelKey,
  type CalendarEvent,
} from "@/lib/calendar-events";
import { useAuthStore } from "@/stores";

/** How many of the next 24 hours the card lists before it defers to the page. */
const SHOWN = 6;

/**
 * Today: meetings, routines and reminders in the next 24 hours, soonest
 * first. One read — the same events route the calendar page uses — and no
 * new endpoint. A lane the read could not reach simply leaves its events
 * out; the card never claims the day is empty on a failed read.
 */
export function CalendarTodayWidget({ title, hint, seeAll, options }: DashboardWidgetProps) {
  const t = useTranslations("dashboard.widgets.calendar-today");
  const tc = useTranslations("pages.calendar");
  const locale = useLocale();
  const accountZone = useAuthStore((state) => state.user?.time_zone);
  const timeZone = calendarTimeZone(accountZone);
  const now = useMemo(() => new Date(), []);
  const from = now.toISOString();
  const to = new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();
  const { events, isLoading, error, refetch } = useCalendarEvents({ from, to });

  if (isLoading) {
    return (
      <WidgetFrame title={title} hint={hint} seeAll={seeAll} options={options}>
        <WidgetSkeleton />
      </WidgetFrame>
    );
  }

  const rows = [...(events?.events ?? [])]
    .sort((a, b) => a.start.localeCompare(b.start))
    .slice(0, SHOWN)
    .map((event) => row(event, t, tc, timeZone, locale));

  return (
    <WidgetFrame title={title} hint={hint} seeAll={seeAll} options={options}>
      {error ? (
        <WidgetErrorBody onRetry={() => refetch()} />
      ) : rows.length === 0 ? (
        <WidgetEmptyBody
          icon={CalendarDays}
          title={t("empty.title")}
          description={t("empty.description")}
        />
      ) : (
        <StatusList rows={rows} />
      )}
    </WidgetFrame>
  );
}

/** One event as a status row: what it is, when, and which source it came from. */
function row(
  event: CalendarEvent,
  t: Translate,
  tc: Translate,
  timeZone: string,
  locale: string,
): StatusRow {
  return {
    id: event.id,
    label: event.title,
    sub: event.frequent ? t("frequent") : formatEventSpan(event, timeZone, locale),
    pill: tc(sourceLabelKey(event.source)),
    tone: "neutral",
  };
}
