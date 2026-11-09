"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  completeAppConnection,
  connectApp,
  disconnectApp,
  getAppConnectionCatalog,
  listAppConnections,
  reconnectApp,
  refreshAppConnection,
} from "@/lib/app-connections-api";
import { updateMcpConnection } from "@/lib/mcp-connections-api";
import { qk } from "@/lib/query-keys";
import { useAuthStore, useOrgStore } from "@/stores";
import type { AppConnectionCallback, AppConnectionScope } from "@/types/app-connections";

export function isCurrentAppConnectionScope(scope: AppConnectionScope): boolean {
  return (
    useAuthStore.getState().user?.id === scope.userId &&
    useOrgStore.getState().activeOrgId === scope.orgId
  );
}

/** Account switches must not publish stale data or follow a previous user's consent URL. */
export function useAppConnections(scope: AppConnectionScope) {
  const client = useQueryClient();
  const key = qk.appConnections.scope(scope.userId, scope.orgId);
  async function inScope<T>(run: () => Promise<T>): Promise<T> {
    if (!isCurrentAppConnectionScope(scope)) throw new DOMException("scope_changed", "AbortError");
    const result = await run();
    if (!isCurrentAppConnectionScope(scope)) throw new DOMException("scope_changed", "AbortError");
    return result;
  }
  const catalog = useQuery({
    queryKey: qk.appConnections.catalog(scope.userId, scope.orgId),
    queryFn: ({ signal }) => inScope(() => getAppConnectionCatalog(signal)),
    retry: false,
  });
  const connections = useQuery({
    queryKey: qk.appConnections.list(scope.userId, scope.orgId),
    queryFn: ({ signal }) => inScope(() => listAppConnections(signal)),
    // Authorization is completed outside this tab; do not reuse a long freshness window.
    staleTime: 0,
    refetchOnWindowFocus: true,
    retry: false,
  });
  async function invalidate() {
    if (!isCurrentAppConnectionScope(scope)) return;
    await Promise.all([
      client.invalidateQueries({ queryKey: key }),
      client.invalidateQueries({ queryKey: qk.mcpConnections.all() }),
      client.invalidateQueries({ queryKey: qk.mcpServers.lists() }),
      client.invalidateQueries({ queryKey: qk.mcpServers.entries() }),
    ]);
  }
  const begin = useMutation({
    mutationFn: (input: { toolkit: string; label?: string; connectionId?: string }) =>
      inScope(() =>
        input.connectionId
          ? reconnectApp(input.connectionId)
          : connectApp({ toolkit: input.toolkit, ...(input.label ? { label: input.label } : {}) }),
      ),
    onSuccess: invalidate,
  });
  const complete = useMutation({
    mutationFn: (input: AppConnectionCallback) => inScope(() => completeAppConnection(input)),
    onSuccess: invalidate,
  });
  const refreshConnection = useMutation({
    mutationFn: (id: string) => inScope(() => refreshAppConnection(id)),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => inScope(() => disconnectApp(id)),
    onSuccess: invalidate,
  });
  const setDefault = useMutation({
    mutationFn: (id: string) => inScope(() => updateMcpConnection(id, { is_default: true })),
    onSuccess: invalidate,
  });
  const enable = useMutation({
    mutationFn: (id: string) => inScope(() => updateMcpConnection(id, { is_enabled: true })),
    onSuccess: invalidate,
  });
  return {
    catalog,
    connections,
    begin,
    complete,
    refreshConnection,
    remove,
    setDefault,
    enable,
    refresh: invalidate,
  };
}
