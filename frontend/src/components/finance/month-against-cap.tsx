"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { Card, CardContent, CardDescription, CardHeader, CardTitle, Figure } from "@/components/ui";
import { HeadroomBar } from "@/components/dashboard/widgets/budget-headroom";
import { formatUsd } from "@/components/dashboard/format";
import { ROUTES } from "@/lib/constants";

/**
 * The month against the organization's ceiling: what has been spent, what is
 * left, and where the ceiling itself is changed.
 *
 * The cap lives on the Members page (`OrgSpendingLimit`), and only a caller
 * holding `budgets:manage` sees the editor there — so the link is offered
 * only to them. Everybody else gets the sentence saying who can change it,
 * rather than a link to a page where the control is hidden.
 */
export function MonthAgainstCap({
  used,
  cap,
  orgId,
  canManageBudgets,
}: {
  /** Calendar-month-to-date spend, in dollars. */
  used: number;
  /** The organization's monthly ceiling, or null for no ceiling. */
  cap: number | null;
  orgId: string | null;
  canManageBudgets: boolean;
}) {
  const t = useTranslations("pages.finance");
  const locale = useLocale();
  // A stored cap of zero or less blocks every run, and a percentage against
  // it is meaningless — it renders as "no cap set" with the month's spend
  // beside it, and the link for whoever may fix it, rather than Infinity%.
  const effectiveCap = cap !== null && cap > 0 ? cap : null;

  if (effectiveCap === null) {
    return (
      <Card>
        <CardHeader className="space-y-1 px-5 pt-4 pb-2">
          <CardTitle className="text-sm">{t("noCapTitle")}</CardTitle>
          <CardDescription className="text-xs">{t("noCapDescription")}</CardDescription>
        </CardHeader>
        <CardContent className="px-5 pb-4">
          <p className="text-sm">
            {t("monthSpent", { amount: formatUsd(used, locale) })}
            {canManageBudgets && orgId !== null ? (
              <>
                <span aria-hidden> · </span>
                <Link
                  href={ROUTES.ORG_MEMBERS(orgId)}
                  className="text-primary underline underline-offset-2"
                >
                  {t("setCap")}
                </Link>
              </>
            ) : null}
          </p>
        </CardContent>
      </Card>
    );
  }

  const left = Math.max(effectiveCap - used, 0);
  return (
    <Card>
      <CardHeader className="space-y-1 px-5 pt-4 pb-2">
        <CardTitle className="text-sm">{t("monthAgainstCapTitle")}</CardTitle>
        <CardDescription className="text-xs">{t("monthAgainstCapDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 px-5 pb-4">
        <Figure
          value={`${Math.round((used / effectiveCap) * 100)}%`}
          caption={t("againstCap", {
            used: formatUsd(used, locale),
            cap: formatUsd(effectiveCap, locale),
            left: formatUsd(left, locale),
          })}
        />
        <HeadroomBar used={used} cap={effectiveCap} />
        {canManageBudgets && orgId !== null ? (
          <p className="text-sm">
            <Link
              href={ROUTES.ORG_MEMBERS(orgId)}
              className="text-primary underline underline-offset-2"
            >
              {t("changeCap")}
            </Link>
          </p>
        ) : (
          <p className="text-muted-foreground text-sm">{t("capNeedsAdmin")}</p>
        )}
      </CardContent>
    </Card>
  );
}
