"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";

/**
 * One panel of the command center: a heading, a one-line description, preview
 * rows, and a footer link naming the destination. The panel itself is not a
 * link - the rows are a preview only, and the foot is where they open - so a
 * panel never nests a link inside the link that is the panel.
 */
export function CommandPanel({
  title,
  description,
  go,
  href,
  children,
}: {
  title: string;
  description: string;
  /** The line at the foot naming the destination. */
  go: string;
  /** Where the foot goes. */
  href: string;
  /** The preview rows. */
  children: ReactNode;
}) {
  return (
    <section className="bg-card flex flex-col rounded-2xl border p-5">
      <h2 className="text-foreground text-base font-semibold">{title}</h2>
      <p className="text-muted-foreground mt-1 text-sm leading-relaxed">{description}</p>
      <div className="divide-border mt-4 flex-1 divide-y border-t">{children}</div>
      <Link
        href={href}
        prefetch={false}
        className="text-primary mt-4 flex items-center gap-1 text-sm font-semibold"
      >
        {go}
        <ArrowRight className="size-4" aria-hidden />
      </Link>
    </section>
  );
}

/** One preview row: a title, and a subline when there is one to say. */
export function PanelRow({ title, subline }: { title: string; subline?: string }) {
  return (
    <span className="block py-2.5 first:pt-3 last:pb-1">
      <span className="text-foreground block truncate text-sm font-medium">{title}</span>
      {subline ? (
        <span className="text-muted-foreground block truncate text-xs">{subline}</span>
      ) : null}
    </span>
  );
}

/** A panel's empty or failed state, in the panel's own voice: no retry here, the destination has it. */
export function PanelNote({ title, description }: { title: string; description: string }) {
  return (
    <span className="block py-2.5 first:pt-3">
      <span className="text-foreground block text-sm font-medium">{title}</span>
      <span className="text-muted-foreground mt-1 block text-xs leading-relaxed">
        {description}
      </span>
    </span>
  );
}
