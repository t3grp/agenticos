import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ruleCounts,
  useEvalTry,
  useFailureLearning,
  useOrgPatterns,
  useProposal,
} from "./use-failure-learning";
import { apiClient } from "@/lib/api-client";

vi.mock("@/lib/api-client", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const emptyList = { items: [], total: 0 };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiClient.get).mockImplementation(async (path: string) => {
    if (path === "/failure-learning/patterns") return { items: [], total: 0, notes: [] };
    if (path.endsWith("/patterns")) return { items: [], total: 0, notes: [] };
    if (path.endsWith("/ledger")) return emptyList;
    if (path.endsWith("/eval-cases")) return emptyList;
    if (path.endsWith("/proposals")) return emptyList;
    if (path.includes("/eval-runs/"))
      return { id: "try-1", agent_id: "agent-1", cases: [], passed: 0, failed: 0, pending: 0 };
    if (path.includes("/proposals/")) return { id: "prop-1", status: "draft" };
    throw new Error(`unexpected GET ${path}`);
  });
  vi.mocked(apiClient.post).mockResolvedValue({ id: "new-1" });
  vi.mocked(apiClient.patch).mockResolvedValue({ id: "entry-1" });
  vi.mocked(apiClient.delete).mockResolvedValue(undefined);
});

describe("useFailureLearning", () => {
  it("reads the agent's patterns, ledger, cases and proposals", async () => {
    const { result } = renderHook(() => useFailureLearning("agent-1"), { wrapper });

    await waitFor(() => expect(result.current.patternsLoading).toBe(false));
    expect(apiClient.get).toHaveBeenCalledWith("/agents/agent-1/failure-learning/patterns");
    expect(apiClient.get).toHaveBeenCalledWith("/agents/agent-1/failure-learning/ledger", {
      params: undefined,
    });
    expect(apiClient.get).toHaveBeenCalledWith("/agents/agent-1/failure-learning/eval-cases");
    expect(apiClient.get).toHaveBeenCalledWith("/agents/agent-1/failure-learning/proposals", {
      params: undefined,
    });
  });

  it("writes through every mutation", async () => {
    const { result } = renderHook(() => useFailureLearning("agent-1"), { wrapper });
    await waitFor(() => expect(result.current.patternsLoading).toBe(false));

    const pattern = {
      agent_id: "agent-1",
      agent_name: "Helper",
      model: null,
      status: "failed",
      error_class: "other",
      pattern_key: "failed:other",
      count: 3,
      first_seen: null,
      last_seen: null,
      sample_run_ids: ["run-1"],
      sample_message_ids: [],
      already_tracked: false,
    };
    result.current.track.mutate({ pattern, input: { title: "T", summary: "S" } });
    result.current.createEntry.mutate({
      scope: "agent",
      pattern_key: "failed:other",
      title: "T",
      summary: "S",
      evidence: {},
    });
    result.current.updateEntry.mutate({ entryId: "entry-1", input: { status: "open" } });
    result.current.removeEntry.mutate("entry-1");
    result.current.createCase.mutate({
      name: "C",
      input: { message: "hi" },
      expectation: { kind: "contains", field: "text", value: "hi" },
    });
    result.current.removeCase.mutate("case-1");
    result.current.createProposal.mutate({
      target: { type: "instructions" },
      old_text: "old",
      new_text: "new",
      rationale: "why",
    });
    result.current.apply.mutate("prop-1");
    result.current.reject.mutate({ proposalId: "prop-1", reason: "no" });
    result.current.startTry.mutate({ case_ids: ["case-1"] });

    await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(7));
    expect(apiClient.post).toHaveBeenCalledWith(
      "/agents/agent-1/failure-learning/ledger",
      expect.objectContaining({ scope: "agent" }),
    );
    expect(apiClient.post).toHaveBeenCalledWith(
      "/agents/agent-1/failure-learning/proposals/prop-1/apply",
    );
    expect(apiClient.post).toHaveBeenCalledWith(
      "/agents/agent-1/failure-learning/proposals/prop-1/reject",
      { reason: "no" },
    );
    expect(apiClient.post).toHaveBeenCalledWith("/agents/agent-1/failure-learning/eval-runs", {
      case_ids: ["case-1"],
    });
    expect(apiClient.patch).toHaveBeenCalledWith(
      "/agents/agent-1/failure-learning/ledger/entry-1",
      { status: "open" },
    );
    expect(apiClient.delete).toHaveBeenCalledWith(
      "/agents/agent-1/failure-learning/ledger/entry-1",
    );
    expect(apiClient.delete).toHaveBeenCalledWith(
      "/agents/agent-1/failure-learning/eval-cases/case-1",
    );
  });

  it("toasts a mutation failure", async () => {
    const { toast } = await import("sonner");
    vi.mocked(apiClient.post).mockRejectedValueOnce(new Error("nope"));
    const { result } = renderHook(() => useFailureLearning("agent-1"), { wrapper });
    await waitFor(() => expect(result.current.patternsLoading).toBe(false));

    result.current.createEntry.mutate({
      scope: "agent",
      pattern_key: "failed:other",
      title: "T",
      summary: "S",
      evidence: {},
    });

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
  });
});

describe("useOrgPatterns", () => {
  it("reads the org patterns and tracks one", async () => {
    const { result } = renderHook(() => useOrgPatterns(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(apiClient.get).toHaveBeenCalledWith("/failure-learning/patterns");
  });
});

describe("useEvalTry", () => {
  it("reads a try, and reads nothing without an id", async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useEvalTry("agent-1", id),
      {
        wrapper,
        initialProps: { id: "try-1" as string | null },
      },
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.try?.id).toBe("try-1");

    rerender({ id: null });
    expect(result.current.try).toBeNull();
  });
});

describe("useProposal", () => {
  it("reads a proposal, and nothing without an id", async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useProposal("agent-1", id),
      { wrapper, initialProps: { id: "prop-1" as string | null } },
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.proposal?.id).toBe("prop-1");

    rerender({ id: null });
    expect(result.current.proposal).toBeNull();
  });
});

describe("ruleCounts", () => {
  it("counts open entries, resolved entries and applied proposals separately", () => {
    expect(
      ruleCounts(
        [{ status: "open" }, { status: "resolved" }, { status: "draft" }, { status: "dismissed" }],
        [{ status: "applied" }, { status: "draft" }],
      ),
    ).toEqual({ openEntries: 2, resolvedEntries: 1, appliedProposals: 1 });
  });
});
