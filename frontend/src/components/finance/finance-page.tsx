"use client";

import { Activity } from "lucide-react";

import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/dashboard/page-header";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { useOrganizationList, usePermissions, useProviderCatalog, useSpend } from "@/hooks";
import { getErrorMessage } from "@/lib/api-error";
import { resolvePreset } from "@/lib/dashboard/period";
import { useOrgStore } from "@/stores";
import { Perm } from "@/types/permissions";
import { MonthAgainstCap } from "./month-against-cap";
import { ProviderSpend } from "./provider-spend";
import { TcoChecklist } from "./tco-checklist";

/**
 * The one honest money page.
 *
 * Month-to-date spend against the organization's cap, this month's estimated
 * spend per provider with a link to each provider's own billing console, the
 * "spend is not your bill" caveat, and the total-cost checklist. It reuses
 * the Spend tab's data — `GET /spend`, asked over the calendar month — and
 * adds no backend endpoint: a finance page that needs a new meter would be
 * a second source of money truth, and this product already has one.
 *
 * Gated on `runs:view`, the permission `GET /spend` carries, so a caller
 * without it is not asked for: the 403 would otherwise draw as a failure on
 * a page that never had anything to show them. There is no "time saved"
 * figure anywhere here — none exists in the codebase to render, and the page
 * does not invent one.
 */
export function FinancePage() {
  const t = useTranslations("pages.finance");
  const tErrors = useTranslations("errors");
  const { can } = usePermissions();
  const canView = can(Perm.runsView);
  // The calendar month, not the dashboard's rolling window: a cap is monthly,
  // and a provider's invoice covers a calendar month.
  const month = resolvePreset("tm");
  const { spend, isLoading, error, refetch } = useSpend(
    { from: month.from, to: month.to },
    { enabled: canView },
  );
  const organizations = useOrganizationList();
  const activeOrgId = useOrgStore((state) => state.activeOrgId);
  const { data: catalog } = useProviderCatalog();
  const providerNames = new Map((catalog?.items ?? []).map((entry) => [entry.id, entry.name]));

  if (!canView) {
    return (
      <EmptyState
        icon={Activity}
        title={t("noAccessTitle")}
        description={t("noAccessDescription")}
      />
    );
  }
  if (isLoading || organizations.isLoading) return <LoadingState variant="skeleton-cards" />;
  if (error || spend === undefined)
    return (
      <ErrorState
        title={t("spendCouldNotBeRead")}
        description={getErrorMessage(error, tErrors, t("spendErrorFallback"))}
        cta={{ label: t("tryAgain"), onClick: () => void refetch() }}
      />
    );

  const organization =
    organizations.data?.find((candidate) => candidate.id === activeOrgId) ??
    organizations.data?.[0];
  const cap =
    organization?.monthly_budget_usd != null ? Number(organization.monthly_budget_usd) : null;

  return (
    <div className="space-y-6">
      <div data-tour="finance-overview">
        <PageHeader title={t("title")} description={t("description")} />
      </div>
      <MonthAgainstCap
        used={Number(spend.month_to_date_usd)}
        cap={cap}
        orgId={organization?.id ?? null}
        canManageBudgets={can(Perm.budgetsManage)}
      />
      <ProviderSpend
        rows={spend.by_provider}
        providerNames={providerNames}
        partialRunCount={spend.partial_run_count}
      />
      <section aria-labelledby="finance-caveat" className="space-y-1">
        <h2 id="finance-caveat" className="text-base font-semibold">
          {t("caveatTitle")}
        </h2>
        <p className="text-muted-foreground text-sm">{t("caveat")}</p>
      </section>
      <TcoChecklist />
    </div>
  );
}
