export interface NativeSubscriptionStatus {
  available: boolean;
  running: boolean;
  connected: boolean;
  auth_method: string | null;
  subscription_type: string | null;
  session_mode: "login" | "session" | null;
}

export type NativeSubscriptionAction = "login" | "start" | "stop" | "sign_out" | "ticket";

export interface NativeSubscriptionInput {
  action: NativeSubscriptionAction;
  cols?: number;
  rows?: number;
}

export interface NativeSubscriptionResult {
  status: NativeSubscriptionStatus;
  ticket?: string;
  expires_in?: number;
}
