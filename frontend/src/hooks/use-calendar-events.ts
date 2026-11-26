"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchCalendarEvents } from "@/lib/calendar-api";
import { qk } from "@/lib/query-keys";

/** The calendar page's window read: events, routines and reminders together. */
export function useCalendarEvents({
  from,
  to,
  providers,
  enabled = true,
}: {
  from: string;
  to: string;
  providers?: string[];
  enabled?: boolean;
}) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.calendar.events(from, to, providers ?? null),
    queryFn: () => fetchCalendarEvents({ from, to, providers }),
    enabled,
  });
  return { events: data, isLoading, error, refetch };
}
