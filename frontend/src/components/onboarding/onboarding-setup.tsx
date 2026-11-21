"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { AuthGuard } from "@/components/layout/auth-guard";
import { FirstRunWizard } from "./first-run-wizard";
import { TeamBuilder } from "./team-builder";

/**
 * Guided onboarding: the five-step team builder, or — with
 * `?flow=first-run` — the four-step guided first run.
 *
 * The team builder stays the default: it is what registration and invitation
 * links point at, and this change extends the shell rather than replacing it.
 * The wizard is reached from the dashboard start checklist, the chat start
 * offer, and the sidebar's "First-run setup" entry.
 */
export function OnboardingSetup({ flow, step }: { flow?: string; step?: string }) {
  const t = useTranslations("firstRun");
  return (
    <AuthGuard>
      {flow === "first-run" ? (
        <FirstRunWizard initialStep={step} />
      ) : (
        <>
          <p className="mx-auto max-w-2xl px-6 pt-6 text-sm">
            <Link href="/onboarding?flow=first-run" className="underline">
              {t("tryWizard")}
            </Link>
          </p>
          <TeamBuilder />
        </>
      )}
    </AuthGuard>
  );
}
