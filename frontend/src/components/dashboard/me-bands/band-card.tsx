"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import type { ComponentType, ReactNode } from "react";

/**
 * One band of the person's own dashboard, as a single link: the whole card
 * is the click, not a word at its end. The rows inside are a preview only -
 * the destination is where each one opens - so a card never nests a link or
 * a button inside the link that is the card.
 */
export function BandCard({
  href,
  icon: Icon,
  title,
  description,
  go,
  children,
}: {
  /** Where the whole card goes. */
  href: string;
  /** The band's glyph, drawn in the accent tile. */
  icon: ComponentType<{ className?: string }>;
  title: string;
  description: string;
  /** The line at the foot naming the destination. */
  go: string;
  /** The preview rows. */
  children: ReactNode;
}) {
  return (
    <Link
      href={href}
      prefetch={false}
      className="group bg-card focus-visible:ring-ring flex flex-col rounded-2xl border p-5 transition-shadow outline-none hover:shadow-md focus-visible:ring-2"
    >
      <span
        className="bg-primary/10 text-primary mb-4 grid size-10 shrink-0 place-items-center rounded-xl"
        aria-hidden
      >
        <Icon className="size-5" />
      </span>
      <span className="text-foreground text-base font-semibold">{title}</span>
      <span className="text-muted-foreground mt-1 text-sm leading-relaxed">{description}</span>
      <span className="divide-border mt-4 block flex-1 divide-y border-t">{children}</span>
      <span className="text-primary mt-4 flex items-center gap-1 text-sm font-semibold">
        {go}
        <ArrowRight
          className="size-4 transition-transform group-hover:translate-x-0.5"
          aria-hidden
        />
      </span>
    </Link>
  );
}

/** One preview row: a title, and a subline when there is one to say. */
export function BandRow({ title, subline }: { title: string; subline?: string }) {
  return (
    <span className="block py-2.5 first:pt-3 last:pb-1">
      <span className="text-foreground block truncate text-sm font-medium">{title}</span>
      {subline ? (
        <span className="text-muted-foreground block truncate text-xs">{subline}</span>
      ) : null}
    </span>
  );
}

/** A band's empty or failed state, in the card's own voice: no retry here, the destination has it. */
export function BandNote({ title, description }: { title: string; description: string }) {
  return (
    <span className="block py-2.5 first:pt-3">
      <span className="text-foreground block text-sm font-medium">{title}</span>
      <span className="text-muted-foreground mt-1 block text-xs leading-relaxed">
        {description}
      </span>
    </span>
  );
}
