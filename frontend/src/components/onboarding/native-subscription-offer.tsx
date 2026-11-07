"use client";

import { skipToken, useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { ConfirmDialog } from "@/components/ui";
import { useTenantId } from "@/hooks/use-organizations";
import { qk } from "@/lib/query-keys";
import { useAuthStore } from "@/stores/auth-store";
import { useOnboardingStore } from "@/stores/onboarding-store";
import type { NativeSubscriptionStatus } from "@/types/native-subscriptions";

/** Offer native setup only once this deployment says a personal session is available. */
export function NativeSubscriptionOffer() {
  const t = useTranslations("onboarding");
  const tenant = useTenantId();
  const user = useAuthStore((state) => state.user);
  const openFlow = useOnboardingStore((state) => state.openFlow);
  const dismissOffer = useOnboardingStore((state) => state.dismissOffer);
  const { data } = useQuery<NativeSubscriptionStatus>({
    queryKey: qk.nativeSubscriptions.mine(`${tenant ?? ""}:${user?.id ?? ""}:personal`),
    queryFn: skipToken,
  });
  if (!data?.available || !user || user.impersonation) return null;
  return (
    <ConfirmDialog
      open
      onOpenChange={(open) => {
        if (!open) dismissOffer();
      }}
      title={t("offer.explore-subscriptions.title")}
      description={t("offer.explore-subscriptions.body")}
      confirmLabel={t("offer.accept")}
      cancelLabel={t("offer.decline")}
      onConfirm={() => openFlow("explore-subscriptions")}
    />
  );
}
