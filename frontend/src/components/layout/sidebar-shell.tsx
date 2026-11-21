"use client";

/** Shared desktop and phone navigation chrome. Organization selection and search
 * stay above the scrollable destinations; notifications and account stay below. */

import Link from "next/link";
import { Settings, SquarePen } from "lucide-react";
import { useTranslations } from "next-intl";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { DesktopInstallPrompt } from "@/components/layout/desktop-install-prompt";
import { ConsoleVersion } from "@/components/layout/console-version";

import { NotificationBell } from "@/components/layout/notification-bell";
import { SidebarUser } from "@/components/layout/sidebar-user";
import { OrganizationSwitcher } from "@/components/layout/organization-switcher";
import { buttonVariants } from "@/components/ui/button";
import { ROUTES } from "@/lib/constants";
import { stripLocale } from "@/lib/active-route";
import { cn } from "@/lib/utils";

export function SidebarShell({
  children,
  collapsed = false,
  onNavigate,
}: {
  children: ReactNode;
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const t = useTranslations("workspaceHome");
  const tNav = useTranslations("nav");
  const pathname = stripLocale(usePathname() ?? "/");
  // Settings is a place, not an account action: it sits in the column's
  // footer, above the account block, lit the same green as a nav row when
  // the page you are on is inside it. The account menu keeps its own
  // "Profile & settings" row - the two lead to the same place, and the
  // footer one is the one somebody finds without opening a menu.
  const settingsActive = pathname === ROUTES.SETTINGS || pathname.startsWith(`${ROUTES.SETTINGS}/`);
  return (
    <>
      <div className={cn("shrink-0 pt-1", collapsed ? "flex justify-center px-1 pb-2" : "px-3")}>
        <OrganizationSwitcher collapsed={collapsed} onNavigate={onNavigate} />
      </div>
      {!collapsed && (
        <div className="flex shrink-0 flex-col gap-2 px-3 pt-3 pb-3">
          <Link
            href="/chat?new=workspace"
            onClick={onNavigate}
            className={cn(
              buttonVariants({ variant: "secondary" }),
              "w-full justify-start gap-2.5 px-3",
            )}
            data-tour="chat-start"
          >
            <SquarePen className="size-4" aria-hidden />
            {t("newChat")}
          </Link>
        </div>
      )}
      <div className="flex min-h-0 flex-1 scrollbar-thin flex-col overflow-y-auto overscroll-contain">
        {children}
      </div>

      {!collapsed && <DesktopInstallPrompt />}
      <div
        className={cn(
          "flex shrink-0 flex-col gap-1 py-2",
          collapsed ? "items-center px-1" : "px-3",
        )}
      >
        {/* The language and the theme used to be
            here too, which filed "set this once" beside "find something" under
            four unlabelled glyphs; they are named rows in the account menu
            below now. */}
        <div className={cn("flex items-center gap-0.5", collapsed && "flex-col")}>
          <NotificationBell variant="icon" />
          <ConsoleVersion collapsed={collapsed} />
        </div>
        <Link
          href={ROUTES.SETTINGS}
          onClick={onNavigate}
          aria-current={settingsActive ? "page" : undefined}
          title={collapsed ? tNav("settings") : undefined}
          aria-label={collapsed ? tNav("settings") : undefined}
          className={cn(
            "focus-visible:ring-ring flex h-9 items-center rounded-lg text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset",
            collapsed ? "w-9 justify-center" : "gap-2.5 px-2.5",
            settingsActive
              ? "bg-brand-subtle text-brand font-semibold"
              : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
          )}
        >
          <Settings className="size-4 shrink-0" aria-hidden />
          {collapsed ? null : <span className="truncate">{tNav("settings")}</span>}
        </Link>
        <SidebarUser compact={collapsed} />
      </div>
    </>
  );
}
