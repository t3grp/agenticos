"use client";

import Link from "next/link";
import { ArrowRight, ArrowUpRight, Plug } from "lucide-react";
import { useTranslations } from "next-intl";

import { useBranding } from "@/components/branding/branding-provider";
import { AuthGuard } from "@/components/layout/auth-guard";
import { Badge, Button } from "@/components/ui";
import { ROUTES } from "@/lib/constants";

/** A small optional first-run step; app authorization stays in Connectors. */
export function OnboardingSetup() {
  const t = useTranslations("onboardingSetup");
  const { appName } = useBranding();

  return (
    <AuthGuard>
      <div className="bg-background text-foreground flex min-h-screen flex-col">
        <header className="border-border border-b">
          <div className="mx-auto flex w-full max-w-4xl items-center px-6 py-4">
            <span className="inline-flex items-center gap-2 text-base font-semibold tracking-tight">
              <span aria-hidden className="bg-brand inline-block h-2.5 w-2.5 rounded-full" />
              {appName}
            </span>
          </div>
        </header>

        <main
          id="main"
          tabIndex={-1}
          className="mx-auto flex w-full max-w-4xl flex-1 flex-col justify-center gap-10 px-6 py-12"
        >
          <section className="max-w-2xl space-y-3">
            <h1 className="text-3xl font-semibold tracking-tight">{t("title", { appName })}</h1>
            <p className="text-muted-foreground text-base leading-relaxed">{t("description")}</p>
          </section>

          <section
            aria-labelledby="onboarding-apps-title"
            className="bg-card border-border space-y-5 rounded-2xl border p-5 sm:p-6"
          >
            <div className="flex items-start gap-4">
              <span className="bg-muted text-muted-foreground mt-0.5 inline-flex size-10 shrink-0 items-center justify-center rounded-xl">
                <Plug aria-hidden className="size-5" />
              </span>
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 id="onboarding-apps-title" className="text-lg font-medium">
                    {t("appsTitle")}
                  </h2>
                  <Badge variant="secondary">{t("optional")}</Badge>
                </div>
                <p className="text-muted-foreground text-sm leading-relaxed">
                  {t("appsDescription")}
                </p>
              </div>
            </div>

            <div className="flex flex-col items-start gap-3 border-t pt-5 sm:flex-row sm:items-center">
              <Button asChild variant="outline">
                <Link href={ROUTES.CONNECTORS} target="_blank" rel="noopener noreferrer">
                  {t("connectApps")}
                  <ArrowUpRight aria-hidden />
                </Link>
              </Button>
              <p className="text-muted-foreground text-xs">{t("opensNewTab")}</p>
            </div>

            <p className="text-muted-foreground border-t pt-4 text-sm leading-relaxed">
              {t("skipHint")}
            </p>
          </section>

          <footer className="flex justify-end border-t pt-6">
            <Button asChild>
              <Link href={ROUTES.CHAT}>
                {t("continueToWorkspace")}
                <ArrowRight aria-hidden />
              </Link>
            </Button>
          </footer>
        </main>
      </div>
    </AuthGuard>
  );
}
