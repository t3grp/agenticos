/**
 * Starring an app: the request method follows the star, the change shows at once
 * in the lists, and a refused change is put back.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useToggleArtifactFavourite } from "./use-artifacts";
import { apiClient, ApiError } from "@/lib/api-client";
import { qk } from "@/lib/query-keys";
import type { Artifact, ArtifactDetail, ArtifactList } from "@/types/artifact";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api-client")>("@/lib/api-client");
  return { ...actual, apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } };
});
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { error: (...args: unknown[]) => toastError(...args) } }));

function artifact(overrides: Partial<Artifact> = {}): ArtifactDetail {
  return {
    id: "a1",
    name: "weekly-report",
    title: "Weekly report",
    visibility: "private",
    owner_user_id: "u1",
    agent_id: "ag1",
    environment_id: null,
    environment_name: null,
    public_url: null,
    live: false,
    published_at: "2026-09-22T10:00:00Z",
    current_version: null,
    archived_at: null,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: null,
    is_favourite: false,
    can_edit: true,
    may_change_owner: false,
    ...overrides,
  } as ArtifactDetail;
}

const FAVOURITES = ["artifacts", "list", { favourites: true }] as const;
const ALL = ["artifacts", "list", { favourites: false }] as const;

function setup() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  client.setQueryData<ArtifactList>(FAVOURITES, {
    items: [artifact({ is_favourite: true })],
    total: 1,
  });
  client.setQueryData<ArtifactList>(ALL, { items: [artifact()], total: 1 });
  client.setQueryData(qk.artifacts.detail("a1"), artifact());
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...renderHook(() => useToggleArtifactFavourite(), { wrapper }) };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useToggleArtifactFavourite", () => {
  it("stars with POST and shows the star in every list and the detail at once", async () => {
    vi.mocked(apiClient.post).mockResolvedValue(artifact({ is_favourite: true }));
    const { result, client } = setup();

    await act(async () => {
      await result.current.mutateAsync({ id: "a1", favourite: true });
    });

    expect(apiClient.post).toHaveBeenCalledWith("/apps/a1/favourite");
    expect(apiClient.delete).not.toHaveBeenCalled();
    expect(client.getQueryData<ArtifactList>(ALL)?.items[0]?.is_favourite).toBe(true);
    expect(client.getQueryData<ArtifactDetail>(qk.artifacts.detail("a1"))?.is_favourite).toBe(true);
  });

  it("unstars with DELETE and drops the app from the favourites list", async () => {
    vi.mocked(apiClient.delete).mockResolvedValue(artifact({ is_favourite: false }));
    const { result, client } = setup();

    await act(async () => {
      await result.current.mutateAsync({ id: "a1", favourite: false });
    });

    expect(apiClient.delete).toHaveBeenCalledWith("/apps/a1/favourite");
    expect(apiClient.post).not.toHaveBeenCalled();
    expect(client.getQueryData<ArtifactList>(FAVOURITES)?.items).toEqual([]);
    expect(client.getQueryData<ArtifactList>(FAVOURITES)?.total).toBe(0);
  });

  it("puts the lists back and says why when the server refuses", async () => {
    vi.mocked(apiClient.post).mockRejectedValue(new ApiError(403, "nope"));
    const { result, client } = setup();

    await act(async () => {
      result.current.mutate({ id: "a1", favourite: true });
    });

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(client.getQueryData<ArtifactList>(ALL)?.items[0]?.is_favourite).toBe(false);
    expect(client.getQueryData<ArtifactList>(FAVOURITES)?.items[0]?.id).toBe("a1");
  });
});
