import { apiClient } from "@/lib/api-client";
import type { AuditEntryList } from "@/types/audit";

/** Rows per page of the activity feed - the endpoint caps at 100, and a page
 * of fifty keeps the join below cheap while the trail grows. */
export const AUDIT_PAGE_SIZE = 50;

/**
 * One page of the organization's audit trail, newest first.
 *
 * The backend renders each entry's plain sentence and resolves the actor's
 * name on read, so the feed never shows a UUID or a machine action code.
 */
export function listAuditEntries(skip: number, limit: number = AUDIT_PAGE_SIZE) {
  return apiClient.get<AuditEntryList>("/audit", {
    params: { skip: String(skip), limit: String(limit) },
  });
}
