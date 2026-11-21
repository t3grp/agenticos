"use client";

import { useLocale, useTranslations } from "next-intl";

import { LoadingState } from "@/components/states";
import type { FeedRow } from "@/lib/audit-feed";

interface ActivityFeedProps {
  rows: readonly FeedRow[];
  isLoading: boolean;
  error: Error | null;
}

/**
 * The activity feed's rows: one plain sentence each, with when it happened
 * and - where the trail names one - who approved it and what the run cost.
 *
 * Presentational only: the page owns the query and the filters, so this never
 * has to tell "nothing happened" from "the request failed" - `error` and an
 * empty `rows` are different props for that reason.
 */
export function ActivityFeed({ rows, isLoading, error }: ActivityFeedProps) {
  const t = useTranslations("pages.activityFeed");
  const locale = useLocale();

  if (isLoading) return <LoadingState variant="pending" />;
  if (error !== null) return <p className="text-destructive text-sm">{t("couldNotBeRead")}</p>;
  if (rows.length === 0) return <p className="text-muted-foreground text-sm">{t("empty")}</p>;

  return (
    <ul className="space-y-3" data-tour="activity-feed">
      {rows.map((row) => (
        <li key={row.id} className="rounded-lg border p-3">
          <p className="text-sm">{row.sentence}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            {row.createdAt !== null && (
              <span>
                {new Date(row.createdAt).toLocaleString(locale, {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
              </span>
            )}
            {row.approvedBy !== null && (
              <span>
                {row.createdAt !== null ? " · " : ""}
                {t("approvedBy", { name: row.approvedBy })}
              </span>
            )}
            {row.costUsd !== null && (
              <span>
                {row.createdAt !== null || row.approvedBy !== null ? " · " : ""}
                {t("runCost", { cost: row.costUsd })}
              </span>
            )}
          </p>
        </li>
      ))}
    </ul>
  );
}
