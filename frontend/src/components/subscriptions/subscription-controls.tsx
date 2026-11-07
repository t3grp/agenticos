"use client";

import { LogIn, LogOut, Play, RefreshCw, Square } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui";
import type {
  NativeSubscriptionAction,
  NativeSubscriptionStatus,
} from "@/types/native-subscriptions";

export function SubscriptionControls({
  status,
  busy,
  attached,
  onAction,
  onRefresh,
  onSignOut,
}: {
  status: NativeSubscriptionStatus;
  busy: boolean;
  attached: boolean;
  onAction: (action: NativeSubscriptionAction) => void;
  onRefresh: () => void;
  onSignOut: () => void;
}) {
  const t = useTranslations("nativeSubscriptions");
  const subscriptionReady = status.connected && status.auth_method === "claude.ai";
  return (
    <div className="flex flex-wrap items-center gap-2" data-tour="subscription-controls">
      {!subscriptionReady && !status.running && (
        <Button disabled={busy} onClick={() => onAction("login")}>
          <LogIn className="size-4" aria-hidden />
          {t("signIn")}
        </Button>
      )}
      {(status.connected || status.running) && (
        <Button disabled={busy || attached} onClick={() => onAction("start")}>
          <Play className="size-4" aria-hidden />
          {t(
            status.running
              ? "reconnect"
              : subscriptionReady
                ? "openSession"
                : "openSeparateBilling",
          )}
        </Button>
      )}
      {status.running && (
        <Button variant="outline" disabled={busy} onClick={() => onAction("stop")}>
          <Square className="size-4" aria-hidden />
          {t("stop")}
        </Button>
      )}
      <Button variant="outline" disabled={busy} onClick={onRefresh}>
        <RefreshCw className="size-4" aria-hidden />
        {t("refresh")}
      </Button>
      {(status.connected || status.running) && (
        <Button variant="ghost" disabled={busy} onClick={onSignOut}>
          <LogOut className="size-4" aria-hidden />
          {t("signOut")}
        </Button>
      )}
    </div>
  );
}
