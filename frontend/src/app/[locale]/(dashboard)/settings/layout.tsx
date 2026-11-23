"use client";

import { glideOrJump } from "@/lib/motion";
import { scrollWithin } from "@/lib/scroll-within";

import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";

import { PageHeader } from "@/components/dashboard/page-header";
import { PageTabs } from "@/components/dashboard/page-tabs";
import { useShell } from "@/hooks/use-shell";
import { useActiveRoute } from "@/lib/active-route";
import { isDesktopShell } from "@/lib/desktop/shell";

import { SETTINGS_GROUPS } from "./settings-tabs";
import { useTranslations } from "next-intl";

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const t = useTranslations("pages.settings");
  const memory = useTranslations("pages.memory");
  const pathname = usePathname();
  const shell = useShell();
  const routeActive = useActiveRoute();
  const isMemory = pathname.endsWith("/settings/memory");
  const start = useRef<HTMLDivElement>(null);
  const previousPath = useRef(pathname);
  useEffect(() => {
    if (previousPath.current === pathname) return;
    previousPath.current = pathname;
    if (start.current) scrollWithin(start.current, "start", glideOrJump());
  }, [pathname]);
  // A group shows when one of its tabs would: the desktop app's own tabs
  // ("This device") stay out of the row in a browser, unless the page you
  // are on is one of them - the same rule PageTabs applies to the tabs
  // themselves, so a heading never sits above an empty row.
  const visibleGroups = SETTINGS_GROUPS.map((group) => ({
    ...group,
    tabs: group.tabs.filter(
      (tab) =>
        isDesktopShell(shell) ||
        !tab.shellOnly ||
        routeActive(tab.href, tab.exact) ||
        (tab.matchHrefs?.some((alias) => routeActive(alias, tab.exact)) ?? false),
    ),
  })).filter((group) => group.tabs.length > 0);
  return (
    <div ref={start} className="flex min-h-0 flex-1 scroll-mt-16 flex-col space-y-4 lg:scroll-mt-6">
      <PageHeader
        title={isMemory ? memory("title") : t("settings")}
        description={isMemory ? memory("description") : t("manageYourAccountIntegrations")}
      />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-5">
        <div data-tour="settings-tabs" className="flex flex-col gap-4">
          {visibleGroups.map((group) => (
            <section key={group.headingKey} aria-label={t(group.headingKey)}>
              <div className="mb-1.5 flex items-center gap-3">
                <h2 className="text-muted-foreground font-mono text-xs font-medium tracking-[0.1em] uppercase">
                  {t(group.headingKey)}
                </h2>
                <span aria-hidden className="bg-border h-px flex-1" />
              </div>
              <PageTabs tabs={group.tabs} overflow="scroll" />
            </section>
          ))}
        </div>
        <div className="flex min-h-0 w-full min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </div>
  );
}
