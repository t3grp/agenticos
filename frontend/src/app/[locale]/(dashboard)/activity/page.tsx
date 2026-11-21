"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { ActivityFeed } from "@/components/activity/activity-feed";
import { PageHeader } from "@/components/dashboard/page-header";
import { ExportMenu } from "@/components/runs/export-menu";
import { LoadingState } from "@/components/states";
import { Button, Input, Label } from "@/components/ui";
import { useAuditFeed } from "@/hooks/use-audit-feed";
import { usePermissions } from "@/hooks";
import { buildFeedRows, feedAgentNames } from "@/lib/audit-feed";
import { AUDIT_PAGE_SIZE } from "@/lib/audit-api";
import { PAGE_CLEARANCE } from "@/lib/page-clearance";
import { cn } from "@/lib/utils";
import { Perm } from "@/types/permissions";

/**
 * What the agents did, in plain sentences: the audit trail as a feed, with
 * the approval each action rode on and what its run cost.
 *
 * The filters narrow the loaded page, not the trail - the endpoint pages by
 * `skip`/`limit` and takes no agent or day parameter, so a day's entries
 * spread over two pages need the pager to reach them. The export is the one
 * read that takes a window: the selected day, or the last seven days.
 */
export default function ActivityPage() {
  const t = useTranslations("pages.activityFeed");
  const { can, isLoading: permissionsLoading } = usePermissions();
  const canRead = can(Perm.auditRead);
  const [skip, setSkip] = useState(0);
  const [query, setQuery] = useState("");
  const [agent, setAgent] = useState("");
  const [day, setDay] = useState("");
  const { entries, total, isLoading, error } = useAuditFeed(skip, { enabled: canRead });

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return buildFeedRows(entries).filter((row) => {
      if (agent !== "" && row.agentName !== agent) return false;
      if (day !== "" && (row.createdAt === null || !row.createdAt.startsWith(day))) return false;
      if (needle === "") return true;
      return (
        row.sentence.toLowerCase().includes(needle) ||
        row.action.toLowerCase().includes(needle) ||
        (row.agentName ?? "").toLowerCase().includes(needle)
      );
    });
  }, [entries, agent, day, query]);
  const agents = useMemo(() => feedAgentNames(buildFeedRows(entries)), [entries]);

  const range = useMemo(() => {
    if (day !== "") {
      return { from: `${day}T00:00:00.000Z`, to: `${day}T23:59:59.999Z` };
    }
    const to = new Date();
    const from = new Date(to.getTime() - 7 * 24 * 60 * 60 * 1000);
    return { from: from.toISOString(), to: to.toISOString() };
  }, [day]);

  if (permissionsLoading) return <LoadingState variant="pending" />;
  if (!canRead) return <p className="text-muted-foreground text-sm">{t("noPermission")}</p>;

  const pageStart = total === 0 ? 0 : skip + 1;
  const pageEnd = Math.min(skip + AUDIT_PAGE_SIZE, total);

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col", PAGE_CLEARANCE)}>
      <PageHeader className="mb-4 shrink-0" title={t("title")} description={t("description")} />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1">
          <Label htmlFor="activity-search">{t("searchLabel")}</Label>
          <Input
            id="activity-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("searchPlaceholder")}
          />
        </div>
        <div>
          <Label htmlFor="activity-agent">{t("agentLabel")}</Label>
          <select
            id="activity-agent"
            value={agent}
            onChange={(event) => setAgent(event.target.value)}
            className="border-input bg-background flex h-9 w-full rounded-md border px-3 py-1 text-sm shadow-sm"
          >
            <option value="">{t("anyAgent")}</option>
            {agents.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="activity-day">{t("dayLabel")}</Label>
          <Input
            id="activity-day"
            type="date"
            value={day}
            onChange={(event) => setDay(event.target.value)}
          />
        </div>
        <ExportMenu
          permission={Perm.auditRead}
          endpoint="/audit/export"
          kind="audit"
          rangeParams={{ from: "created_from", to: "created_to" }}
          range={range}
        />
      </div>
      <ActivityFeed rows={rows} isLoading={isLoading} error={error} />
      {total > AUDIT_PAGE_SIZE && (
        <div className="mt-4 flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={skip === 0}
            onClick={() => setSkip((current) => Math.max(0, current - AUDIT_PAGE_SIZE))}
          >
            {t("previousPage")}
          </Button>
          <span className="text-muted-foreground text-sm">
            {t("showing", { from: pageStart, to: pageEnd, total })}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={skip + AUDIT_PAGE_SIZE >= total}
            onClick={() => setSkip((current) => current + AUDIT_PAGE_SIZE)}
          >
            {t("nextPage")}
          </Button>
        </div>
      )}
    </div>
  );
}
