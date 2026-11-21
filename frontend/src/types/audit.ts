/** One recorded action, as `GET /audit` returns it. */

export interface AuditEntry {
  id: string;
  actor_user_id: string | null;
  impersonator_user_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown> | null;
  created_at: string | null;
  /** The actor's display name, resolved on read - the log stores UUIDs only. */
  actor_name: string | null;
  /** The entry in plain language, rendered on read. */
  sentence: string | null;
}

export interface AuditEntryList {
  items: AuditEntry[];
  total: number;
}
