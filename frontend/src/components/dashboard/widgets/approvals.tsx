"use client";

import { NeedsYouCompact } from "../needs-you-compact";
import { WidgetFrame } from "../widget-frame";
import type { DashboardWidgetProps } from "./types";

/** Tool calls parked on a person. This card earns its place by being empty. */
export function ApprovalsWidget({ title, hint, seeAll, options }: DashboardWidgetProps) {
  return (
    <WidgetFrame title={title} hint={hint} seeAll={seeAll} options={options}>
      <NeedsYouCompact />
    </WidgetFrame>
  );
}
