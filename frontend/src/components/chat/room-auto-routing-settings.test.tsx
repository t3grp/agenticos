import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { useConversationStore } from "@/stores";
import type { ModelProfile } from "@/types/providers";
import type { RoomParticipantRead } from "@/types/room";
import type { RoomAutoRoutingPolicyRead } from "@/types/room-auto-routing";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RoomAutoRoutingSettings } from "./room-auto-routing-settings";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  identity: { accountId: "member-a", organizationId: "org-a", tenant: "org-a" },
  candidates: { value: null as unknown },
  models: { value: null as unknown },
}));

vi.mock("next-intl", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next-intl")>();
  return {
    ...actual,
    useTranslations: (namespace?: string) =>
      Object.assign((key: string) => (namespace ? `${namespace}.${key}` : key), {
        rich: (key: string) => (namespace ? `${namespace}.${key}` : key),
        markup: (key: string) => (namespace ? `${namespace}.${key}` : key),
        has: () => true,
      }),
  };
});

vi.mock("@/hooks/use-room", () => ({
  useRoomIdentity: () => {
    const { accountId, organizationId, tenant } = mocks.identity;
    return {
      accountId,
      organizationId,
      tenant,
      current: () =>
        mocks.identity.accountId === accountId &&
        mocks.identity.organizationId === organizationId &&
        mocks.identity.tenant === tenant,
    };
  },
}));
vi.mock("@/lib/room-auto-routing-api", () => ({
  getRoomAutoRoutingPolicy: mocks.get,
  putRoomAutoRoutingPolicy: mocks.put,
}));
vi.mock("@/hooks/use-room-auto-routing-candidates", () => ({
  useRoomAutoRoutingCandidates: () => mocks.candidates.value,
}));
vi.mock("@/hooks/use-model-providers", () => ({
  useModelProviders: () => mocks.models.value,
}));
vi.mock("@/components/chat/chat-model-menu", () => ({
  ChatModelMenu: ({
    profiles,
    value,
    onChange,
  }: {
    profiles: Pick<ModelProfile, "id" | "label">[];
    value: string | null;
    onChange: (profileId: string) => void;
  }) => (
    <div role="radiogroup" aria-label="Saved organization models">
      {profiles.map((profile) => (
        <button
          key={profile.id}
          type="button"
          role="radio"
          aria-checked={profile.id === value}
          onClick={() => onChange(profile.id)}
        >
          {profile.label}
        </button>
      ))}
    </div>
  ),
}));

let queryClient: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function policy(overrides: Partial<RoomAutoRoutingPolicyRead> = {}): RoomAutoRoutingPolicyRead {
  return {
    enabled: false,
    revision: 0,
    can_edit: true,
    router_agent_id: null,
    router_version_id: null,
    model_profile_id: null,
    quiet_window_ms: 2500,
    max_responders: 1,
    ...overrides,
  };
}

function participant(id: string, autoReply = false): RoomParticipantRead {
  return {
    kind: "agent",
    id,
    name: id === "mention-only" ? "Mention-only router" : "Opted-in responder",
    avatar_url: null,
    is_member: true,
    can_add: false,
    can_mention: true,
    auto_reply: autoReply,
  };
}

function profile(id: string, usage_scope: "organization" | "personal"): ModelProfile {
  return {
    id,
    label: usage_scope === "organization" ? "Organization profile" : "Personal profile",
    provider: "openai",
    model: "gpt-test",
    usage_scope,
    secret_id: "secret",
    params: {},
    fallback_profile_ids: [],
    chat_capable: true,
    missing_key: false,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

function configureButton() {
  return screen.getByRole("button", { name: "chat.roomAutoRouting.configure" });
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mocks.identity = { accountId: "member-a", organizationId: "org-a", tenant: "org-a" };
  mocks.get.mockResolvedValue(policy());
  mocks.candidates.value = {
    candidates: [participant("mention-only"), participant("opted-in", true)],
    isLoading: false,
    error: null,
    retry: vi.fn(),
  };
  mocks.models.value = {
    profiles: [profile("org-profile", "organization"), profile("personal-profile", "personal")],
    catalog: [],
    profilesStatus: "loaded",
    isLoading: false,
    refetchProfiles: vi.fn(),
  };
  useConversationStore.setState({ currentConversationId: "room-a", selectionVersion: 4 });
});

describe("RoomAutoRoutingSettings", () => {
  it("hides Configure after the server confirms the viewer cannot edit", async () => {
    const pending = deferred<RoomAutoRoutingPolicyRead>();
    const denied = policy({ can_edit: false });
    mocks.get.mockReturnValueOnce(pending.promise);
    render(<RoomAutoRoutingSettings conversationId="room-a" />, { wrapper });

    expect(configureButton()).toBeDisabled();
    await act(async () => {
      pending.resolve(denied);
      await pending.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "chat.roomAutoRouting.configure" })).toBeNull(),
    );
    expect(screen.getByText("chat.roomAutoRouting.statusDisabled")).toBeInTheDocument();
  });

  it("selects only an organization model and accepts a mention-only router", async () => {
    const current = policy({ revision: 7 });
    const saved = policy({
      ...current,
      enabled: true,
      revision: 8,
      router_agent_id: "mention-only",
      router_version_id: "pinned-version",
      model_profile_id: "org-profile",
    });
    mocks.get.mockResolvedValue(current);
    mocks.put.mockResolvedValue(saved);
    useConversationStore.getState().setCurrentConversationId("room-a");
    render(<RoomAutoRoutingSettings conversationId="room-a" />, { wrapper });

    await waitFor(() => expect(configureButton()).toBeEnabled());
    await userEvent.click(configureButton());
    await userEvent.click(screen.getByRole("switch", { name: "chat.roomAutoRouting.enabled" }));
    await userEvent.click(
      screen.getByRole("combobox", { name: "chat.roomAutoRouting.routerAgent" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Mention-only router" }));

    expect(screen.getByRole("radio", { name: "Organization profile" })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "Personal profile" })).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: "Organization profile" }));
    await userEvent.click(screen.getByRole("button", { name: "chat.roomAutoRouting.save" }));

    await waitFor(() =>
      expect(mocks.put).toHaveBeenCalledWith("room-a", {
        expected_revision: 7,
        enabled: true,
        router_agent_id: "mention-only",
        model_profile_id: "org-profile",
        quiet_window_ms: 2500,
        max_responders: 1,
      }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("requires a router and organization profile to be selected together", async () => {
    useConversationStore.getState().setCurrentConversationId("room-a");
    render(<RoomAutoRoutingSettings conversationId="room-a" />, { wrapper });

    await waitFor(() => expect(configureButton()).toBeEnabled());
    await userEvent.click(configureButton());
    await userEvent.click(
      screen.getByRole("combobox", { name: "chat.roomAutoRouting.routerAgent" }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Mention-only router" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "chat.roomAutoRouting.selectionPairRequired",
    );
    expect(screen.getByRole("button", { name: "chat.roomAutoRouting.save" })).toBeDisabled();
    expect(mocks.put).not.toHaveBeenCalled();
  });

  it("lets managers disable routing while preserving an unavailable saved pair", async () => {
    const current = policy({
      enabled: true,
      revision: 12,
      router_agent_id: "retired-router",
      router_version_id: "retired-version",
      model_profile_id: "retired-profile",
    });
    const saved = policy({ ...current, enabled: false, revision: 13 });
    mocks.get.mockResolvedValue(current);
    mocks.put.mockResolvedValue(saved);
    useConversationStore.getState().setCurrentConversationId("room-a");
    render(<RoomAutoRoutingSettings conversationId="room-a" />, { wrapper });

    await waitFor(() => expect(configureButton()).toBeEnabled());
    await userEvent.click(configureButton());
    await userEvent.click(screen.getByRole("switch", { name: "chat.roomAutoRouting.enabled" }));
    const save = screen.getByRole("button", { name: "chat.roomAutoRouting.save" });
    expect(save).toBeEnabled();
    await userEvent.click(save);

    await waitFor(() =>
      expect(mocks.put).toHaveBeenCalledWith("room-a", {
        expected_revision: 12,
        enabled: false,
        router_agent_id: "retired-router",
        model_profile_id: "retired-profile",
        quiet_window_ms: 2500,
        max_responders: 1,
      }),
    );
  });

  it("blocks cancel, Escape, and outside dismissal while a policy save is pending", async () => {
    const pending = deferred<RoomAutoRoutingPolicyRead>();
    mocks.get.mockResolvedValue(policy());
    mocks.put.mockReturnValueOnce(pending.promise);
    useConversationStore.getState().setCurrentConversationId("room-a");
    render(<RoomAutoRoutingSettings conversationId="room-a" />, { wrapper });

    await waitFor(() => expect(configureButton()).toBeEnabled());
    await userEvent.click(configureButton());
    await userEvent.click(screen.getByRole("button", { name: "chat.roomAutoRouting.save" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "chat.roomAutoRouting.cancel" })).toBeDisabled(),
    );
    expect(screen.getByRole("button", { name: "common.close" })).toBeDisabled();

    await userEvent.keyboard("{Escape}");
    const overlay = screen.getByRole("dialog").previousElementSibling;
    expect(overlay).toHaveAttribute("data-state", "open");
    await userEvent.click(overlay!);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      pending.resolve(policy({ revision: 1 }));
      await pending.promise;
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await waitFor(() => expect(configureButton()).toBeEnabled());
    await userEvent.click(configureButton());
    const settledOverlay = screen.getByRole("dialog").previousElementSibling;
    expect(settledOverlay).toHaveAttribute("data-state", "open");
    await userEvent.click(settledOverlay!);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it.each(["account", "organization", "room"] as const)(
    "closes the editor when its %s scope changes and does not reopen when it returns",
    async (boundary) => {
      const view = render(<RoomAutoRoutingSettings conversationId="room-a" />, { wrapper });
      await waitFor(() => expect(configureButton()).toBeEnabled());
      await userEvent.click(configureButton());
      expect(screen.getByRole("dialog")).toBeInTheDocument();

      if (boundary === "account") mocks.identity.accountId = "member-b";
      if (boundary === "organization") {
        mocks.identity.organizationId = "org-b";
        mocks.identity.tenant = "org-b";
      }
      if (boundary === "room") {
        act(() => useConversationStore.getState().setCurrentConversationId("room-b"));
      }
      const changedRoom = boundary === "room" ? "room-b" : "room-a";
      view.rerender(<RoomAutoRoutingSettings conversationId={changedRoom} />);
      expect(screen.queryByRole("dialog")).toBeNull();

      if (boundary === "account") mocks.identity.accountId = "member-a";
      if (boundary === "organization") {
        mocks.identity.organizationId = "org-a";
        mocks.identity.tenant = "org-a";
      }
      if (boundary === "room") {
        act(() => useConversationStore.getState().setCurrentConversationId("room-a"));
      }
      view.rerender(<RoomAutoRoutingSettings conversationId="room-a" />);
      expect(screen.queryByRole("dialog")).toBeNull();
    },
  );
});
