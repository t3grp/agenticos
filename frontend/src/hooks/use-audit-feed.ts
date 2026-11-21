"use client";

import { useQuery } from "@tanstack/react-query";

import { usePermissions } from "@/hooks/use-permissions";
import { useTenantId } from "@/hooks/use-organizations";
import { listAuditEntries } from "@/lib/audit-api";
import { qk } from "@/lib/query-keys";
import { Perm } from "@/types/permissions";

/**
 * One page of the activity feed: the organization's audit trail, newest first.
 *
 * Read only for a caller holding `audit:read`, which owners and admins hold;
 * anyone else reads nothing and makes no request, so a refusal never renders
 * as an empty feed. The query key carries the page's `skip`, so paging back
 * does not serve the first page's rows under the second page's key.
 */
export function useAuditFeed(skip: number, options?: { enabled?: boolean }) {
  const orgId = useTenantId();
  const permissions = usePermissions();
  const allowed = permissions.can(Perm.auditRead);
  const query = useQuery({
    queryKey: qk.organizations.audit(orgId ?? "", skip),
    queryFn: () => listAuditEntries(skip),
    enabled: (options?.enabled ?? true) && allowed && orgId !== null,
  });
  return {
    entries: query.data?.items ?? [],
    total: query.data?.total ?? 0,
    isLoading: permissions.isLoading || query.isLoading,
    error: permissions.error ?? query.error,
    refetch: !permissions.isLoaded ? () => permissions.refetch() : () => query.refetch(),
  };
}
