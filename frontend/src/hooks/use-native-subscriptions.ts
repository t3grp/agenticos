"use client";

import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useTenantGuard, useTenantId } from "@/hooks/use-organizations";
import { changeNativeSubscription, getNativeSubscription } from "@/lib/native-subscriptions-api";
import { qk } from "@/lib/query-keys";
import { useAuthStore } from "@/stores";
import type { NativeSubscriptionInput } from "@/types/native-subscriptions";

interface SessionScope {
  tenant: string | null;
  user: string | undefined;
}

export function useNativeSubscriptions() {
  const tenant = useTenantId();
  const user = useAuthStore((state) => state.user?.id);
  const impersonation = useAuthStore((state) => state.user?.impersonation);
  const guard = useTenantGuard();
  const client = useQueryClient();
  const scope = `${tenant ?? ""}:${user ?? ""}:${impersonation ? "impersonating" : "personal"}`;
  const ready = !!tenant && !!user && !impersonation;
  const status = useQuery({
    queryKey: qk.nativeSubscriptions.mine(scope),
    queryFn: ({ signal }) => getNativeSubscription(tenant!, signal),
    enabled: ready,
    refetchInterval: (query) => (query.state.data?.running ? 10_000 : false),
  });
  const change = useMutation({
    mutationFn: (input: NativeSubscriptionInput) => changeNativeSubscription(tenant!, input),
    onMutate: (): SessionScope => ({ tenant, user }),
    onSuccess: (result, _input, started) => {
      if (
        started &&
        guard(started.tenant) &&
        useAuthStore.getState().user?.id === started.user &&
        !useAuthStore.getState().user?.impersonation
      )
        client.setQueryData(
          qk.nativeSubscriptions.mine(`${started.tenant ?? ""}:${started.user ?? ""}:personal`),
          result.status,
        );
    },
  });
  const authorize = useCallback(async () => {
    if (!tenant || !user || useAuthStore.getState().user?.impersonation) return null;
    const result = await changeNativeSubscription(tenant, { action: "ticket" });
    if (
      !guard(tenant) ||
      useAuthStore.getState().user?.id !== user ||
      useAuthStore.getState().user?.impersonation
    )
      return null;
    return result.ticket ?? null;
  }, [tenant, user, guard]);
  return { status, change, authorize, scope, ready, impersonation: !!impersonation };
}
