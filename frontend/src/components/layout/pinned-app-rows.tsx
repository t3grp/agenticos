"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { usePinnedArtifacts } from "@/hooks/use-artifacts";
import { usePermissions } from "@/hooks/use-permissions";
import { stripLocale } from "@/lib/active-route";
import { ROUTES } from "@/lib/constants";
import { cn } from "@/lib/utils";
import { Perm } from "@/types/permissions";

/**
 * The apps the caller pinned, one row each, under the sidebar's Pinned heading.
 *
 * A row is a tile with the page's initial and its title - the tile alone on
 * the rail, where the row keeps its height so the column and the rail line up.
 * The tile mirrors `ProjectAvatar`'s extra-small tile; apps carry no icon of
 * their own, so the initial is what tells them apart. Nothing renders while
 * the caller has no pins, so the heading stays hidden too.
 */
export function PinnedAppRows({
  onNavigate,
  collapsed,
}: {
  onNavigate?: () => void;
  collapsed: boolean;
}) {
  const { can } = usePermissions();
  const pathname = stripLocale(usePathname());
  const { artifacts } = usePinnedArtifacts(can(Perm.artifactsView));

  return artifacts.map((artifact) => {
    const href = ROUTES.ARTIFACT_DETAIL(artifact.id);
    const active = pathname === href || pathname.startsWith(`${href}/`);
    const initial = Array.from(artifact.title.trim())[0]?.toUpperCase() ?? "·";
    return (
      <Link
        key={artifact.id}
        href={href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        title={collapsed ? artifact.title : undefined}
        aria-label={collapsed ? artifact.title : undefined}
        className={cn(
          "touch:min-h-11 flex h-8 items-center rounded-xl text-sm font-medium transition-colors",
          collapsed ? "touch:min-w-11 w-8 justify-center" : "gap-2.5 px-2.5",
          active
            ? "bg-foreground/8 text-foreground"
            : "text-muted-foreground hover:bg-foreground/5 hover:text-foreground",
        )}
      >
        <span
          aria-hidden
          className="bg-foreground/[0.07] text-foreground inline-flex size-[18px] shrink-0 items-center justify-center rounded-[5px] text-[10px] leading-none font-semibold"
        >
          {initial}
        </span>
        {collapsed ? null : <span className="min-w-0 flex-1 truncate">{artifact.title}</span>}
      </Link>
    );
  });
}
