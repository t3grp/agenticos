"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui";
import { GOAL_TOOLKITS, type AppConnectionGoal } from "@/lib/app-connection-journey";
import type { AppConnection, ConnectedAppDefinition } from "@/types/app-connections";
import { AppConnectionIcon } from "./app-connection-icon";

export function ConnectionCatalog({
  apps,
  connections,
  goal,
  requestedToolkit,
  busy,
  onConnect,
}: {
  apps: ConnectedAppDefinition[];
  connections: AppConnection[];
  goal: AppConnectionGoal;
  requestedToolkit: string | null;
  busy: boolean;
  onConnect: (app: ConnectedAppDefinition) => void;
}) {
  const t = useTranslations("appConnections");
  const [showAll, setShowAll] = useState(false);
  const preferred =
    requestedToolkit && GOAL_TOOLKITS[goal].includes(requestedToolkit)
      ? [requestedToolkit]
      : GOAL_TOOLKITS[goal];
  const recommended = apps.filter((app) => preferred.includes(app.toolkit));
  const shown = showAll || recommended.length === 0 ? apps : recommended;
  return (
    <section className="space-y-4" aria-label={t("catalogTitle")}>
      <div>
        <h2 className="text-lg font-medium" data-tour="app-connection-catalog">
          {t("catalogTitle")}
        </h2>
        <p className="text-muted-foreground mt-1 text-sm">{t("catalogHint")}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((app) => (
          <article
            key={app.key}
            className="border-border flex min-w-0 flex-col gap-4 rounded-xl border p-4"
          >
            <div className="flex items-start gap-3">
              <AppConnectionIcon toolkit={app.toolkit} />
              <div className="min-w-0">
                <h3 className="text-sm font-medium">{app.name}</h3>
                <p className="text-muted-foreground mt-1 text-sm">
                  {t.has(`toolkits.${app.toolkit}`)
                    ? t(`toolkits.${app.toolkit}`)
                    : app.description}
                </p>
              </div>
            </div>
            <div className="mt-auto">
              <Button
                variant="outline"
                size="sm"
                disabled={busy || !app.configured}
                aria-label={t("connectNamed", { name: app.name })}
                onClick={() => onConnect(app)}
              >
                {app.configured
                  ? t(
                      connections.some((item) => item.toolkit === app.toolkit)
                        ? "addAccount"
                        : "connect",
                    )
                  : t("notConfigured")}
              </Button>
            </div>
          </article>
        ))}
      </div>
      {!showAll && shown.length < apps.length && (
        <Button variant="ghost" onClick={() => setShowAll(true)}>
          {t("browseAll")}
        </Button>
      )}
      {shown.length === 0 && <p className="text-muted-foreground text-sm">{t("noApps")}</p>}
    </section>
  );
}
