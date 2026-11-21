"use client";

import { createPortal } from "react-dom";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";
import { ArrowUpRight, X, type LucideIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { RotatingWorkspaceArt } from "@/components/illustrations/rotating-workspace-art";
import { cn } from "@/lib/utils";

export interface WelcomeSuggestion {
  key: string;
  icon: LucideIcon;
  title: string;
  /** What is sent when it is picked, shown under the title unless `description` is. */
  prompt: string;
  /** What the card says under its title, where that is not the prompt itself; empty for nothing. */
  description?: string;
  /** Offered as a small × on the card - a pinned prompt taken off. */
  onRemove?: () => void;
  /** The heading this card sits under, already translated; omitted leaves it ungrouped. */
  group?: string;
  /** A door to a page instead of a prompt - the card links rather than sends. */
  href?: string;
}

interface ChatWelcomeProps {
  /** Above the title: an icon tile, or the avatar of whoever answers. */
  mark: ReactNode;
  title: string;
  lead: ReactNode;
  suggestions: readonly WelcomeSuggestion[];
  onPick: (prompt: string) => void;
  /** Under the cards: hints, a note. */
  footer?: ReactNode;
  /** A narrow window - the Architect's, the Builder's test panel: one column, tighter. */
  compact?: boolean;
  quiet?: boolean;
  loading?: boolean;
  /** The main chat places its hints below the composer. Undefined keeps them inline. */
  suggestionsSlot?: HTMLElement | null;
}

/**
 * An empty conversation, the way `/chat` draws one (#2075): a mark, a title, a
 * line saying what answers here, and cards that start a conversation.
 *
 * One component so the main chat, the AI Architect's window and the Builder's
 * test panel open on the same page rather than three that drifted apart.
 */
export function ChatWelcome({
  mark,
  title,
  lead,
  suggestions,
  onPick,
  footer,
  compact = false,
  quiet = false,
  loading = false,
  suggestionsSlot,
}: ChatWelcomeProps) {
  const t = useTranslations("chat.empty");
  // Suggestions arrive in order, grouped where the caller grouped them - the
  // empty chat's Ask / Do / Track under the same verbs as home - and ungrouped
  // where it did not (an agent's own starters, the visual prompts), which
  // render exactly as before, with no heading. Consecutive cards sharing a
  // group share its heading.
  const groups: { label: string | null; items: WelcomeSuggestion[] }[] = [];
  for (const suggestion of suggestions) {
    const label = suggestion.group ?? null;
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(suggestion);
    else groups.push({ label, items: [suggestion] });
  }
  // The centered composer must stay put when agent metadata replaces skeletons.
  // Reserve four two-line suggestions and the same header slots in both states.
  const gridClass = (first: boolean) =>
    cn(
      "grid",
      quiet
        ? `${first ? "mt-4" : "mt-3"} auto-rows-[3.5rem] gap-1 sm:grid-cols-2`
        : compact
          ? `${first ? "mt-4" : "mt-3"} auto-rows-fr gap-1.5`
          : `${first ? "mt-6" : "mt-3"} auto-rows-fr gap-2 sm:grid-cols-2`,
    );
  const hints = (
    <>
      {loading ? (
        <div aria-busy={loading} className={gridClass(true)}>
          {Array.from({ length: 4 }, (_, index) => (
            <div key={index} className="flex h-full min-h-9 items-center gap-3" aria-hidden>
              <span className="bg-muted size-4 animate-pulse rounded" />
              <span className="bg-muted h-3 w-48 animate-pulse rounded" />
            </div>
          ))}
        </div>
      ) : (
        groups.map((group, index) => (
          <Fragment key={group.label ?? "ungrouped"}>
            {group.label ? (
              <h3 className="text-muted-foreground mt-6 flex items-center gap-2 font-mono text-xs font-medium tracking-[0.1em] uppercase">
                {group.label}
                <span className="bg-border h-px min-w-6 flex-1" aria-hidden />
              </h3>
            ) : null}
            <div aria-busy={loading} className={gridClass(index === 0 && group.label === null)}>
              {group.items.map((suggestion) => {
                const cardClass = cn(
                  "group hover:bg-muted focus-visible:ring-ring flex h-full w-full items-start gap-3 rounded-lg text-left transition-colors outline-none focus-visible:ring-2",
                  quiet ? "text-muted-foreground px-2 py-2" : "bg-muted/60",
                  quiet ? "px-2 py-2" : compact ? "px-3 py-2.5" : "px-3 py-3",
                );
                // A filled card rather than a hairline box: four outlines on a
                // blank page read as a form to fill in, not as places to start.
                const body = (
                  <>
                    {/* Off in a narrow column, where it took a third of a card's
                  width and wrapped the title onto two lines. */}
                    <span
                      className={
                        quiet
                          ? "text-muted-foreground flex size-5 shrink-0 items-center justify-center"
                          : "bg-foreground/[0.045] text-foreground/80 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl @max-xl:hidden"
                      }
                    >
                      <suggestion.icon className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          "block text-sm",
                          quiet ? "line-clamp-2" : "text-foreground font-medium",
                        )}
                      >
                        {suggestion.title}
                      </span>
                      {suggestion.description !== "" && (
                        <span
                          className={cn(
                            "text-muted-foreground mt-0.5 block text-[13px] leading-relaxed",
                            quiet && "sr-only",
                            // A narrow window shows four cards and a composer at once only
                            // when each says its prompt in a line.
                            compact ? "line-clamp-1" : "line-clamp-1 md:line-clamp-2",
                          )}
                        >
                          {suggestion.description ?? suggestion.prompt}
                        </span>
                      )}
                    </span>
                    {suggestion.onRemove ? (
                      // Room for the × that sits over this corner.
                      <span className="h-4 w-4 shrink-0" />
                    ) : (
                      <ArrowUpRight className="text-muted-foreground group-hover:text-foreground mt-0.5 h-4 w-4 shrink-0 opacity-0 transition-[opacity,color] group-hover:opacity-100 group-focus-visible:opacity-100" />
                    )}
                  </>
                );
                return (
                  <div key={suggestion.key} className="relative h-full">
                    {suggestion.href ? (
                      <Link href={suggestion.href} className={cardClass}>
                        {body}
                      </Link>
                    ) : (
                      <button
                        type="button"
                        onClick={() => onPick(suggestion.prompt)}
                        className={cardClass}
                      >
                        {body}
                      </button>
                    )}
                    {suggestion.onRemove && (
                      <button
                        type="button"
                        aria-label={t("unpin", { prompt: suggestion.title })}
                        onClick={suggestion.onRemove}
                        className="text-muted-foreground hover:text-foreground hover:bg-accent absolute top-2.5 right-2.5 rounded-md p-1"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </Fragment>
        ))
      )}

      {footer && (
        // Keyboard hints, so not on a phone - where they also ran into the
        // caption under the composer.
        <div className="text-muted-foreground mt-5 hidden flex-wrap items-center justify-start gap-x-2 gap-y-1 text-xs md:[@media(min-height:761px)]:flex">
          {footer}
        </div>
      )}
    </>
  );
  return (
    <div className={cn("@container mx-auto w-full", compact ? "px-3 py-2" : "py-6 md:py-8")}>
      <div className={compact ? "text-center" : "text-left"}>
        <div
          className={cn(
            "flex",
            compact
              ? "mb-3 justify-center"
              : "relative mb-4 min-h-36 w-full items-center justify-start",
            quiet && !compact && "min-h-36",
          )}
        >
          {!compact && <RotatingWorkspaceArt />}
          <span className="relative">{mark}</span>
        </div>
        <h2
          className={cn(
            "text-foreground min-h-9 font-medium tracking-tight",
            quiet && "min-h-[2lh] md:min-h-9",
            compact ? "text-lg" : "text-2xl md:text-3xl",
          )}
        >
          {title}
        </h2>
        <p
          className={cn(
            "text-muted-foreground line-clamp-2 max-w-xl leading-relaxed",
            quiet && "min-h-[2lh]",
            compact ? "mt-1 text-[13px]" : "mt-2 text-sm",
          )}
        >
          {lead}
        </p>
      </div>

      {suggestionsSlot === undefined
        ? hints
        : suggestionsSlot && createPortal(hints, suggestionsSlot)}
    </div>
  );
}

/** The icon tile `/chat` puts above its title. */
export function WelcomeIcon({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span className="bg-muted text-foreground flex h-12 w-12 items-center justify-center rounded-2xl">
      <Icon className="h-5 w-5" />
    </span>
  );
}
