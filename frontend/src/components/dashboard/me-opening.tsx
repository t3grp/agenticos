"use client";

import { Spinner } from "@/components/ui/spinner";
import { useTranslations } from "next-intl";

import { StartChecklist } from "./start-checklist";

/**
 * What opens below the bands on a person's own dashboard: the start
 * checklist, for a person still setting up. Once it is done or closed it
 * renders nothing, and the bands stand on their own. The briefing used to
 * sit beside it here; it is a preview row now, in the Do card below.
 */
export function MeOpening({ orgId, pending = false }: { orgId: string | null; pending?: boolean }) {
  const states = useTranslations("dashboard.states");
  if (pending)
    return (
      <div
        role="status"
        aria-busy="true"
        aria-label={states("loading")}
        className="bg-card flex h-[26rem] items-center justify-center rounded-2xl border"
      >
        <Spinner className="text-muted-foreground size-5" aria-hidden />
      </div>
    );
  if (!orgId) return null;
  return <StartChecklist orgId={orgId} className="mt-0" />;
}
