"use client";

import { Lightbulb, X } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button, Card, CardContent } from "@/components/ui";
import { usePermissions } from "@/hooks";
import { ROUTES } from "@/lib/constants";
import { Perm, type Permission } from "@/types/permissions";

/** One tip: what it shows, where it leads, and who it is worth showing to. */
interface Tip {
  id: string;
  href: string;
  /** Shown only to a reader who can act on it; none is everybody. */
  gate?: Permission;
}

const TIPS: readonly Tip[] = [
  { id: "sandbox", href: ROUTES.AGENTS, gate: Perm.agentsEdit },
  { id: "subagents", href: ROUTES.AGENTS, gate: Perm.agentsEdit },
  { id: "apps", href: ROUTES.ARTIFACTS, gate: Perm.artifactsView },
  { id: "routines", href: ROUTES.ROUTINES, gate: Perm.agentsEdit },
  { id: "channels", href: ROUTES.CHANNELS, gate: Perm.channelsManage },
  { id: "browser", href: ROUTES.AGENTS, gate: Perm.agentsEdit },
  { id: "knowledge", href: ROUTES.RAG, gate: Perm.agentsEdit },
  { id: "skills", href: ROUTES.SKILLS, gate: Perm.agentsEdit },
  // Keyboard tips remain available after the illustrated product examples.
  { id: "mentions", href: ROUTES.CHAT },
  { id: "commands", href: ROUTES.CHAT },
  { id: "mcp", href: ROUTES.SETTINGS_MCP },
];

const DISMISSED_KEY = "agenticos:discover-tips:dismissed";

function readDismissed(): ReadonlySet<string> {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(DISMISSED_KEY) ?? "[]");
    return new Set(Array.isArray(stored) ? stored.filter((id) => typeof id === "string") : []);
  } catch {
    return new Set();
  }
}

/**
 * What the platform can do that a person would not find by looking: `@` and `/`
 * in the chat, its own MCP server, sandboxes, apps, subagents, routines and the
 * rest - one tip at a time, in a single row at the foot of the person's own
 * dashboard, instead of the carousel it used to be. Dismissing a tip shows the
 * next one; the row goes once every tip has. Which ones are closed is kept in
 * this browser, as the checklist's closing is. A tip about something the reader
 * cannot use is not offered at all.
 */
export function DiscoverTips() {
  const t = useTranslations("dashboard.discover");
  const { can } = usePermissions();
  const [dismissed, setDismissed] = useState(readDismissed);

  const tips = TIPS.filter((tip) => !dismissed.has(tip.id) && (!tip.gate || can(tip.gate)));

  if (tips.length === 0) return null;
  const tip = tips[0]!;
  const title = t(`tips.${tip.id}.title`);

  const dismiss = (id: string) => {
    const next = new Set([...dismissed, id]);
    setDismissed(next);
    try {
      window.localStorage.setItem(DISMISSED_KEY, JSON.stringify([...next]));
    } catch {
      // Nowhere to keep it; closed for this visit.
    }
  };

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-x-3 gap-y-1 p-4">
        <Lightbulb className="text-muted-foreground size-4 shrink-0" aria-hidden />
        <p className="min-w-0 flex-1 text-sm">
          <span className="font-medium">{title}</span>
          <span className="text-muted-foreground"> · {t(`tips.${tip.id}.body`)}</span>
        </p>
        <Link
          href={tip.href}
          className="text-foreground shrink-0 text-sm font-medium hover:underline"
        >
          {t(`tips.${tip.id}.cta`)}
        </Link>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          aria-label={t("dismiss", { title })}
          onClick={() => dismiss(tip.id)}
        >
          <X className="h-4 w-4" />
        </Button>
      </CardContent>
    </Card>
  );
}
