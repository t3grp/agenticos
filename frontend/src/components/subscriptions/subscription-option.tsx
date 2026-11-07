"use client";

import { ArrowRight, TerminalSquare } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";

import { ROUTES } from "@/lib/constants";
import { useOrgStore } from "@/stores/org-store";

/** A different execution path, never a selectable or saved API model. */
export function SubscriptionOption() {
  const t = useTranslations("nativeSubscriptions");
  const organization = useOrgStore((state) => state.activeOrgId);
  return (
    <Link
      href={organization ? `${ROUTES.SUBSCRIPTIONS}?org=${organization}` : ROUTES.SUBSCRIPTIONS}
      className="border-border hover:bg-muted/40 flex items-start gap-3 rounded-xl border p-3 transition-colors"
      data-tour="subscription-option"
    >
      <TerminalSquare className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{t("optionTitle")}</span>
        <span className="text-muted-foreground mt-1 block text-xs leading-relaxed">
          {t("optionDescription")}
        </span>
      </span>
      <ArrowRight className="text-muted-foreground mt-0.5 size-4 shrink-0" aria-hidden />
    </Link>
  );
}
