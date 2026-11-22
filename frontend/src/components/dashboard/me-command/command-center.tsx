"use client";

import { WIDGETS } from "@/lib/dashboard/registry";
import type { SectionDef } from "@/lib/dashboard/layouts";
import type { Period } from "@/lib/dashboard/period";
import type { Permission } from "@/types/permissions";
import { ActivityPanel } from "./activity-panel";
import { AppsPanel } from "./apps-panel";
import { CommandActions } from "./command-actions";
import { NeedsPanel } from "./needs-panel";
import { SpendPanel } from "./spend-panel";
import { StatusStrip } from "./status-strip";

/**
 * The person's own dashboard as a command center, not a questionnaire: a
 * status strip first, the controls as buttons, then the four panels - what
 * needs them, what happened, their apps, what it cost. A panel renders only
 * when its section survived the page's gate and filter and its own data is
 * something the caller may read - the sections are the layout's, the gates
 * are the registry's. A viewer keeps Your apps, on the shared shelf alone.
 */
export function CommandCenter({
  sections,
  period,
  can,
  isAppAdmin,
}: {
  /** The gated, filtered sections - a panel's section must be among them. */
  sections: SectionDef[];
  period: Period;
  can: (permission: Permission) => boolean;
  isAppAdmin: boolean;
}) {
  const has = (id: string) => sections.some((section) => section.id === id);
  const canRun = WIDGETS["conversations"].gate(can, isAppAdmin);
  const showNeeds = has("needs") && WIDGETS["waiting-on-me"].gate(can, isAppAdmin);
  const showActivity = has("activity") && canRun;
  const showApps =
    has("apps") &&
    (WIDGETS["my-top-agents"].gate(can, isAppAdmin) || WIDGETS["my-agents"].gate(can, isAppAdmin));
  const showSpend = has("spend") && WIDGETS["my-summary"].gate(can, isAppAdmin);
  if (!showNeeds && !showActivity && !showApps && !showSpend) return null;

  return (
    <div>
      {canRun ? <StatusStrip period={period} /> : null}
      <div className="mt-4">
        <CommandActions can={can} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        {showNeeds ? <NeedsPanel /> : null}
        {showActivity ? <ActivityPanel canRun={canRun} /> : null}
        {showApps ? <AppsPanel period={period} canRun={canRun} /> : null}
        {showSpend ? <SpendPanel period={period} /> : null}
      </div>
    </div>
  );
}
