import { apiClient } from "./api-client";

export interface BrowserTeachingStep {
  event_id: string;
  sequence: number;
  action: "click" | "input_parameter" | "navigate" | "scroll" | "checkpoint";
  origin: string;
  target_role?: string | null;
  target_fingerprint?: string | null;
  parameter?: string | null;
  reviewed_label?: string | null;
}

export interface BrowserTeaching {
  id: string;
  agent_id: string;
  run_id: string;
  call_id: string;
  lease_id: string;
  origin: string;
  status: string;
  pause_reason: string | null;
  revision: number;
  steps: BrowserTeachingStep[];
  parameters: { event_id: string; name: string; label: string }[];
  proposal_id: string | null;
  expires_at: string;
}

export function startBrowserTeaching(runId: string, callId: string, leaseId: string, key: string) {
  return apiClient.post<BrowserTeaching>(
    "/browser-teachings",
    { run_id: runId, call_id: callId, lease_id: leaseId },
    { headers: { "Idempotency-Key": key } },
  );
}

export function getBrowserTeaching(id: string) {
  return apiClient.get<BrowserTeaching>(`/browser-teachings/${id}`);
}

export function setBrowserTeaching(id: string, action: "pause" | "resume", revision: number) {
  return apiClient.patch<BrowserTeaching>(`/browser-teachings/${id}`, { action, revision });
}

export function stopBrowserTeaching(id: string) {
  return apiClient.post<BrowserTeaching>(`/browser-teachings/${id}/stop`);
}

export function reviewBrowserTeaching(
  id: string,
  revision: number,
  steps: { event_id: string; reviewed_label: string }[],
  parameters: { event_id: string; name: string; label: string }[],
) {
  return apiClient.put<BrowserTeaching>(`/browser-teachings/${id}/review`, {
    revision,
    steps,
    parameters,
  });
}

export function proposeBrowserTeaching(id: string, name: string, description: string) {
  return apiClient.post<BrowserTeaching>(`/browser-teachings/${id}/propose`, { name, description });
}

export function discardBrowserTeaching(id: string) {
  return apiClient.delete<void>(`/browser-teachings/${id}`);
}
