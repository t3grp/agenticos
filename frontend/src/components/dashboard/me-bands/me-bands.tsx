"use client";

import { useTranslations } from "next-intl";

import { WIDGETS } from "@/lib/dashboard/registry";
import type { SectionDef } from "@/lib/dashboard/layouts";
import type { Period } from "@/lib/dashboard/period";
import type { Permission } from "@/types/permissions";
import { AskBand } from "./ask-band";
import { DoBand } from "./do-band";
import { TrackBand } from "./track-band";

/**
 * The person's own dashboard as three cards, not three grids of cards: one
 * click per band, and each card carries its own preview rows, so the page
 * shows everything at a glance.
 *
 * A band renders only when its section survived the page's gate and filter
 * and its own data is something the caller may read - the sections are the
 * layout's, the gates are the registry's. A viewer keeps Ask, on the shared
 * shelf alone: the conversations read needs `agents:run`, the agents list
 * only `agents:view`.
 */
export function MeBands({
  sections,
  period,
  can,
  isAppAdmin,
}: {
  /** The gated, filtered sections - a band's section must be among them. */
  sections: SectionDef[];
  period: Period;
  can: (permission: Permission) => boolean;
  isAppAdmin: boolean;
}) {
  const t = useTranslations("dashboard.bands");
  const has = (id: string) => sections.some((section) => section.id === id);
  const canRun = WIDGETS["conversations"].gate(can, isAppAdmin);
  const canViewRuns = WIDGETS["needs-attention"].gate(can, isAppAdmin);
  const showAsk = has("ask") && (canRun || WIDGETS["my-agents"].gate(can, isAppAdmin));
  const showDo = has("do") && (WIDGETS["suggestions"].gate(can, isAppAdmin) || canViewRuns);
  const showTrack = has("track") && WIDGETS["waiting-on-me"].gate(can, isAppAdmin);
  if (!showAsk && !showDo && !showTrack) return null;

  return (
    <div>
      <h2 className="flex items-center gap-2">
        <span className="text-muted-foreground truncate font-mono text-xs font-medium tracking-[0.1em] uppercase">
          {t("label")}
        </span>
        <span className="bg-border h-px min-w-6 flex-1" aria-hidden />
      </h2>
      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-3">
        {showAsk ? <AskBand period={period} canRun={canRun} /> : null}
        {showDo ? <DoBand period={period} canRun={canRun} canViewRuns={canViewRuns} /> : null}
        {showTrack ? <TrackBand period={period} /> : null}
      </div>
    </div>
  );
}
