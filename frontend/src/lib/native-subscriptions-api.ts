import { apiClient } from "@/lib/api-client";
import type {
  NativeSubscriptionInput,
  NativeSubscriptionResult,
  NativeSubscriptionStatus,
} from "@/types/native-subscriptions";

export const getNativeSubscription = (organizationId: string, signal?: AbortSignal) =>
  apiClient.get<NativeSubscriptionStatus>("/native-subscriptions", {
    headers: { "X-Organization-Id": organizationId },
    signal,
  });

export const changeNativeSubscription = (organizationId: string, input: NativeSubscriptionInput) =>
  apiClient.post<NativeSubscriptionResult>("/native-subscriptions", input, {
    headers: { "X-Organization-Id": organizationId, "X-Native-Session": "1" },
  });
