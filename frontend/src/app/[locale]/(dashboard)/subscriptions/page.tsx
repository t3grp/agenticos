"use client";

import { TerminalSquare } from "lucide-react";
import { useTranslations } from "next-intl";

import { PageHeader } from "@/components/dashboard/page-header";
import { SubscriptionWorkspace } from "@/components/subscriptions/subscription-workspace";

export default function SubscriptionsPage() {
  const t = useTranslations("nativeSubscriptions");
  return (
    <div className="mx-auto w-full max-w-6xl space-y-5">
      <PageHeader title={t("title")} description={t("description")} />
      <div className="flex items-start gap-3" data-tour="subscription-introduction">
        <TerminalSquare className="text-muted-foreground mt-0.5 size-5 shrink-0" aria-hidden />
        <div className="space-y-2">
          <h2 className="text-base font-medium">{t("sessionTitle")}</h2>
          <p className="text-muted-foreground max-w-3xl text-sm leading-relaxed">
            {t("scopeHelp")}
          </p>
          <p className="text-muted-foreground max-w-3xl text-sm leading-relaxed">
            {t("billingHelp")}
          </p>
        </div>
      </div>
      <SubscriptionWorkspace />
    </div>
  );
}
