"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { ErrorState, LoadingState } from "@/components/states";
import { Badge, ConfirmDialog } from "@/components/ui";
import {
  NativeTerminal,
  type TerminalConnection,
} from "@/components/subscriptions/native-terminal";
import { SubscriptionControls } from "@/components/subscriptions/subscription-controls";
import { useNativeSubscriptions } from "@/hooks/use-native-subscriptions";
import { getErrorMessage } from "@/lib/api-error";
import type { NativeSubscriptionAction } from "@/types/native-subscriptions";

export function SubscriptionWorkspace() {
  const actions = useNativeSubscriptions();
  return <ScopedWorkspace key={actions.scope} actions={actions} />;
}

function ScopedWorkspace({ actions }: { actions: ReturnType<typeof useNativeSubscriptions> }) {
  const t = useTranslations("nativeSubscriptions");
  const errors = useTranslations("errors");
  const [ticket, setTicket] = useState<string | null>(null);
  const [connection, setConnection] = useState<TerminalConnection>("closed");
  const [failure, setFailure] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const { refetch } = actions.status;
  const refresh = useCallback(() => {
    void refetch();
  }, [refetch]);
  const perform = async (action: NativeSubscriptionAction) => {
    setFailure(null);
    try {
      const result = await actions.change.mutateAsync({ action, cols: 120, rows: 32 });
      if (!mounted.current) return;
      if (result.ticket) setTicket(result.ticket);
      if (action === "sign_out") {
        setTicket(null);
        setSigningOut(false);
      }
    } catch (error) {
      if (mounted.current) setFailure(getErrorMessage(error, errors));
    }
  };
  if (actions.impersonation)
    return <p className="text-muted-foreground text-sm">{t("ownAccountOnly")}</p>;
  if (!actions.ready || actions.status.isPending)
    return <LoadingState variant="skeleton-list" rows={2} />;
  if (actions.status.isError)
    return (
      <ErrorState
        title={t("loadFailed")}
        description={getErrorMessage(actions.status.error, errors)}
        cta={{ label: t("refresh"), onClick: refresh }}
      />
    );
  const status = actions.status.data;
  if (!status.available) return <p className="text-muted-foreground text-sm">{t("unavailable")}</p>;
  const openHelp =
    status.auth_method === "claude.ai" ? "openSessionHelp" : "openSeparateBillingHelp";
  const emptyHelp = status.running ? "reconnectHelp" : status.connected ? openHelp : "signInHelp";
  return (
    <section className="border-border space-y-4 rounded-xl border p-4 sm:p-5">
      <div className="flex flex-wrap items-center gap-2" data-tour="subscription-account">
        <Badge variant={status.connected ? "secondary" : "outline"}>
          {status.connected ? t("signedIn") : t("signedOut")}
        </Badge>
        {status.connected && status.auth_method === "claude.ai" && status.subscription_type && (
          <span className="text-muted-foreground text-sm">
            {t("plan", { plan: status.subscription_type })}
          </span>
        )}
        {status.connected && status.auth_method !== "claude.ai" && (
          <span className="text-muted-foreground text-sm">{t("accountBilling")}</span>
        )}
      </div>
      <SubscriptionControls
        status={status}
        busy={actions.change.isPending}
        attached={connection === "open" || connection === "connecting"}
        onAction={(action) => void perform(action)}
        onRefresh={refresh}
        onSignOut={() => setSigningOut(true)}
      />
      {failure && (
        <p role="alert" className="text-destructive text-sm">
          {failure}
        </p>
      )}
      {ticket ? (
        <div className="space-y-2">
          <p role="status" className="text-muted-foreground text-xs">
            {t(`connection.${connection}`)}
          </p>
          <NativeTerminal
            ticket={ticket}
            authorize={actions.authorize}
            onConnection={setConnection}
            onEnd={refresh}
          />
        </div>
      ) : (
        <p className="text-muted-foreground text-sm leading-relaxed">{t(emptyHelp)}</p>
      )}
      <p className="text-muted-foreground text-xs leading-relaxed">{t("sessionHelp")}</p>
      <ConfirmDialog
        open={signingOut}
        onOpenChange={setSigningOut}
        title={t("signOutTitle")}
        description={t("signOutHelp")}
        confirmLabel={t("signOut")}
        loading={actions.change.isPending}
        onConfirm={() => perform("sign_out")}
      />
    </section>
  );
}
