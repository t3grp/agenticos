import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { useCodingAccountUsage } from "./use-coding-account-usage";
import { useAuthStore } from "@/stores";
import { qk } from "@/lib/query-keys";
import type { CodingAccount, CodingAccountUsage } from "@/lib/coding-accounts-api";
import * as api from "@/lib/coding-accounts-api";

const audience = vi.hoisted(() => ({ tenant: "org-1" as string | null }));
vi.mock("@/hooks/use-organizations", () => ({
  useTenantId: () => audience.tenant,
  useTenantGuard: () => (tenant: string | null) => audience.tenant === tenant,
}));
vi.mock("@/lib/coding-accounts-api", () => ({
  readCodingAccountUsage: vi.fn(),
  refreshCodingAccountUsage: vi.fn(),
}));

const person = (id = "user-1") => ({
  id,
  email: `${id}@example.test`,
  is_active: true,
  created_at: "2026-11-01T00:00:00Z",
});
function account(changes: Partial<CodingAccount> = {}): CodingAccount {
  return {
    id: "account-1",
    harness: "codex",
    label: "Personal Codex",
    secret_kind: "openai_codex",
    enabled: true,
    generation: "generation-1",
    is_default: true,
    pool_member: false,
    usage_refresh_pending: false,
    usage: {
      status: "unknown",
      observed_at: null,
      observation_age_seconds: null,
      valid_until: null,
      source: "unavailable",
      windows: [],
      reason_code: "telemetry_unavailable",
    },
    model_catalog: {
      status: "unknown",
      source: "unavailable",
      observed_at: null,
      valid_until: null,
      model_ids: [],
      reason_code: "model_catalog_unverified",
    },
    ...changes,
  };
}
function report(row: CodingAccount, marker: string): CodingAccountUsage {
  return {
    account_id: row.id,
    generation: row.generation,
    usage: {
      ...row.usage,
      status: "available",
      source: "native_runtime",
      observed_at: new Date().toISOString(),
      valid_until: new Date(Date.now() + 300_000).toISOString(),
      observation_age_seconds: 0,
      reason_code: null,
      windows: [{ name: marker, used: 10, limit: 100, unit: "%", resets_at: null }],
    },
    model_catalog: row.model_catalog,
    refresh_pending: false,
  };
}
function mount(initial = account()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    client,
    ...renderHook((row: CodingAccount) => useCodingAccountUsage(row), {
      wrapper,
      initialProps: initial,
    }),
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  audience.tenant = "org-1";
  useAuthStore.setState({ user: person() });
  vi.mocked(api.readCodingAccountUsage).mockResolvedValue(report(account(), "current"));
  vi.mocked(api.refreshCodingAccountUsage).mockResolvedValue(report(account(), "refreshed"));
});

function nextAudience(change: string, initial: CodingAccount) {
  return {
    row: {
      ...initial,
      id: change === "account" ? "account-2" : initial.id,
      generation: change === "generation" ? "generation-2" : initial.generation,
    },
    tenant: change === "organization" ? "org-2" : "org-1",
    user: change === "owner" ? "user-2" : "user-1",
  };
}

it.each(["account", "organization", "owner", "generation"])(
  "keeps a delayed cached read out of the next %s's report",
  async (change) => {
    const initial = account();
    let finish!: (value: CodingAccountUsage) => void;
    vi.mocked(api.readCodingAccountUsage).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result, client, rerender } = mount(initial);
    await waitFor(() => expect(finish).toBeDefined());
    const next = nextAudience(change, initial);
    vi.mocked(api.readCodingAccountUsage).mockResolvedValue(report(next.row, "next audience"));
    act(() => {
      audience.tenant = next.tenant;
      useAuthStore.setState({ user: person(next.user) });
      rerender(next.row);
    });
    await waitFor(() =>
      expect(result.current.report?.usage.windows[0]?.name).toBe("next audience"),
    );
    await act(async () => {
      finish(report(initial, "previous audience"));
    });
    const oldKey = qk.codingAccounts.usage("org-1:user-1", initial.id, initial.generation);
    await waitFor(() => expect(client.getQueryState(oldKey)?.fetchStatus).toBe("idle"));
    const nextKey = qk.codingAccounts.usage(
      `${next.tenant}:${next.user}`,
      next.row.id,
      next.row.generation,
    );
    expect(client.getQueryData<CodingAccountUsage>(nextKey)?.usage.windows[0]?.name).toBe(
      "next audience",
    );
    expect(result.current.report?.usage.windows[0]?.name).toBe("next audience");
  },
);

it.each(["account", "organization", "owner", "generation"])(
  "does not attach an earlier refresh result after the %s changes",
  async (change) => {
    const initial = account();
    let finish!: (value: CodingAccountUsage) => void;
    vi.mocked(api.refreshCodingAccountUsage).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { result, client, rerender } = mount(initial);
    await waitFor(() => expect(result.current.checking).toBe(false));
    act(() => result.current.requestRefresh());
    await waitFor(() => expect(finish).toBeDefined());
    const next = nextAudience(change, initial);
    vi.mocked(api.readCodingAccountUsage).mockResolvedValue(report(next.row, "next audience"));
    act(() => {
      audience.tenant = next.tenant;
      useAuthStore.setState({ user: person(next.user) });
      rerender(next.row);
    });
    await waitFor(() =>
      expect(result.current.report?.usage.windows[0]?.name).toBe("next audience"),
    );
    await act(async () => {
      finish({ ...report(initial, "previous refresh"), refresh_pending: true });
    });
    expect(result.current.report?.usage.windows[0]?.name).toBe("next audience");
    expect(result.current.pending).toBe(false);
    const nextKey = qk.codingAccounts.usage(
      `${next.tenant}:${next.user}`,
      next.row.id,
      next.row.generation,
    );
    expect(client.getQueryData<CodingAccountUsage>(nextKey)?.refresh_pending).toBe(false);
    expect(api.refreshCodingAccountUsage).toHaveBeenCalledWith({
      id: initial.id,
      generation: initial.generation,
    });
  },
);

it.each(["organization", "owner"])("does not read or refresh without the %s", async (missing) => {
  if (missing === "organization") audience.tenant = null;
  else useAuthStore.setState({ user: null });
  const { result } = mount();
  expect(result.current.report).toBeUndefined();
  expect(result.current.ready).toBe(false);
  act(() => result.current.requestRefresh());
  await waitFor(() => expect(result.current.changed).toBe(true));
  expect(api.readCodingAccountUsage).not.toHaveBeenCalled();
  expect(api.refreshCodingAccountUsage).not.toHaveBeenCalled();
});

it.each(["account", "generation"])(
  "hides a response that belongs to another %s",
  async (change) => {
    const wrong = nextAudience(change, account()).row;
    vi.mocked(api.readCodingAccountUsage).mockResolvedValue(report(wrong, "wrong evidence"));
    const { result, client } = mount();
    const invalidated = vi.spyOn(client, "invalidateQueries");
    await waitFor(() => expect(result.current.changed).toBe(true));
    expect(result.current.report).toBeUndefined();
    expect(invalidated).toHaveBeenCalledWith({
      queryKey: qk.codingAccounts.mine("org-1:user-1"),
      exact: true,
    });
    const cached = client.getQueryData<CodingAccountUsage>(
      qk.codingAccounts.usage("org-1:user-1", "account-1", "generation-1"),
    );
    expect(cached?.usage.windows).toEqual([]);
  },
);

it("prevents a slower cached read from replacing an accepted refresh", async () => {
  let finish!: (value: CodingAccountUsage) => void;
  vi.mocked(api.readCodingAccountUsage).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  vi.mocked(api.refreshCodingAccountUsage).mockResolvedValue({
    ...report(account(), "accepted refresh"),
    refresh_pending: true,
  });
  const { result } = mount();
  await waitFor(() => expect(finish).toBeDefined());
  act(() => result.current.requestRefresh());
  await waitFor(() => expect(result.current.pending).toBe(true));
  await act(async () => {
    finish(report(account(), "earlier cached read"));
  });
  expect(result.current.report?.usage.windows[0]?.name).toBe("accepted refresh");
  expect(result.current.pending).toBe(true);
  expect(api.refreshCodingAccountUsage).toHaveBeenCalledTimes(1);
});
