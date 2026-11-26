"use client";

import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ROUTES } from "@/lib/constants";
import {
  formatEventSpan,
  formatEventTime,
  sourceDotClass,
  sourceLabelKey,
  type CalendarEvent,
} from "@/lib/calendar-events";
import { cn } from "@/lib/utils";

/**
 * Where a routine or reminder links to: the agent that owns it, whose page
 * lists its routines. The backend carries no console URL for a routine, so
 * the frontend builds the link; an external event carries its own provider
 * URL instead.
 */
export function routineLink(event: CalendarEvent): string | null {
  if (event.source !== "routine" && event.source !== "reminder") return null;
  return event.agent_id ? ROUTES.AGENT_DETAIL(event.agent_id) : null;
}

/**
 * One event's detail popover: title, time in the user's zone, source, agent
 * (routines and reminders), and the provider or routine link. Read-only —
 * nothing here edits, fires or reschedules anything.
 */
export function EventDetailPopover({
  event,
  timeZone,
  locale,
}: {
  event: CalendarEvent;
  timeZone: string;
  locale: string;
}) {
  const t = useTranslations("pages.calendar");
  // An external event carries its own provider URL; a routine or reminder
  // links to the agent that owns it. `target` only for the external one.
  const link = event.url ?? routineLink(event);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex w-full items-center gap-1.5 truncate rounded px-1 py-0.5 text-left text-xs",
            "hover:bg-accent focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
          )}
        >
          <span
            aria-hidden
            className={cn("size-2 shrink-0 rounded-full", sourceDotClass(event.source))}
          />
          <span className="truncate">
            {event.frequent
              ? event.title
              : `${formatEventTime(event.start, timeZone, locale)} ${event.title}`}
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80">
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">{event.title}</h3>
          <p className="text-muted-foreground text-sm">
            {event.frequent ? t("frequentNote") : formatEventSpan(event, timeZone, locale)}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{t(sourceLabelKey(event.source))}</Badge>
            {event.recurring && <Badge variant="outline">{t("recurring")}</Badge>}
          </div>
          {event.agent_name && (
            <p className="text-sm">{t("agentName", { name: event.agent_name })}</p>
          )}
          {link && (
            <a
              href={link}
              target={event.url ? "_blank" : undefined}
              rel={event.url ? "noreferrer" : undefined}
              className="text-primary text-sm underline underline-offset-2"
            >
              {t("openLink")}
            </a>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
