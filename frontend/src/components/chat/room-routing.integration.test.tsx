import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as intl from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api-client";
import { ApiError } from "@/lib/api-error";
import { announceRoomChange } from "@/lib/room-events";
import { useAuthStore, useChatStore, useConversationStore, useOrgStore } from "@/stores";
import type { Agent } from "@/types/agents";
import type { RoomLeadPolicy, RoomRoutingProposal, RoomTaskCard } from "@/types/room-routing";
import { RoomRoutingPanel } from "./room-routing-panel";

vi.mock("@/lib/api-client", () => ({ apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));
let agents: Agent[];
vi.mock("@/hooks/use-agents", () => ({
  useAgents: () => ({ agents, isLoading: false, error: null }),
}));
let client: QueryClient;
let policy: RoomLeadPolicy;
let proposals: RoomRoutingProposal[];
let tasks: RoomTaskCard[];
const pin = { agent_id: "writer", version_id: "published-7", name: "Writer", current: false };
const user = (id = "me") => ({ id, email: `${id}@example.com`, is_active: true, created_at: "" });
function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
function agent(id: string, changes: Partial<Agent> = {}): Agent {
  return {
    id,
    slug: id,
    name: id,
    status: "published",
    can_run: true,
    visibility: "private",
    owner_user_id: "me",
    current_version_id: "version",
    description: null,
    ...changes,
  };
}
function proposal(changes: Partial<RoomRoutingProposal> = {}): RoomRoutingProposal {
  return {
    id: "proposal-a",
    conversation_id: "room-a",
    thread_root_message_id: null,
    submission_id: "submission-a",
    message_id: "message-a",
    requester_id: "me",
    lead_run_id: "run-a",
    policy_revision: 7,
    revision: 1,
    decision: "pending",
    accepted_item_ids: [],
    can_accept: true,
    can_discard: true,
    created_at: "2026-11-16T00:00:00Z",
    items: [
      {
        proposal_item_id: "item-a",
        title: "Review the invoice batch",
        expected_result: "A checked reconciliation",
        accountable_user_id: "me",
        due_at: "2026-11-17T09:00:00Z",
        agent: pin,
        existing_task_id: null,
        expected_task_revision: null,
      },
    ],
    ...changes,
  };
}
function task(changes: Partial<RoomTaskCard> = {}): RoomTaskCard {
  return {
    id: "link-a",
    proposal_id: "proposal-a",
    proposal_item_id: "item-a",
    message_id: "message-a",
    thread_root_message_id: null,
    task_id: "task-a",
    table_id: "board-a",
    accepted_revision: 1,
    revision: 2,
    title: "Reconcile invoices",
    description: "Review the prepared reconciliation",
    status: "waiting_for_user",
    next_actor: "user",
    accountable_user_id: "me",
    agent: pin,
    due_at: "2026-11-17T09:00:00Z",
    ...changes,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
function changed() {
  announceRoomChange({ conversationId: "room-a", accountId: "me", organizationId: "org-a" });
}
async function openPanel(props: Partial<React.ComponentProps<typeof RoomRoutingPanel>> = {}) {
  const interaction = userEvent.setup();
  const rendered = render(
    <RoomRoutingPanel conversationId="room-a" readOnly={false} {...props} />,
    { wrapper },
  );
  await interaction.click(screen.getByRole("button", { name: /Lead & tasks/ }));
  await screen.findByText("Lead: Writer");
  return { interaction, ...rendered };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(intl, "useFormatter").mockReturnValue(
    intl.createFormatter({ locale: "en", timeZone: "UTC" }),
  );
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  useAuthStore.setState({ user: user() });
  useOrgStore.setState({ activeOrgId: "org-a" });
  useConversationStore.setState({ currentConversationId: "room-a" });
  useChatStore.getState().clearRoomDrafts();
  agents = [agent("writer", { name: "Writer" }), agent("analyst", { name: "Analyst" })];
  policy = {
    enabled: true,
    revision: 7,
    lead: pin,
    specialists: [],
    max_items: 3,
    can_edit: true,
    can_ask: true,
  };
  proposals = [proposal()];
  tasks = [];
  vi.mocked(apiClient.get).mockImplementation(async (path) => {
    if (path === "/orgs") return { items: [] };
    if (path.endsWith("/lead-policy")) return policy;
    if (path.endsWith("/routing-proposals")) return { items: proposals, has_more: false };
    if (path.endsWith("/task-links")) return { items: tasks, has_more: false };
    if (path.endsWith("/routing-proposals/proposal-a")) return proposals[0];
    throw new Error(`Unexpected GET ${path}`);
  });
  vi.mocked(apiClient.put).mockImplementation(async () => policy);
  vi.mocked(apiClient.post).mockImplementation(async (path, input) => {
    const value = input as { selected_item_ids?: string[] };
    proposals = [
      proposal({
        decision: path.endsWith("/accept") ? "accepted" : "discarded",
        revision: 2,
        accepted_item_ids: value.selected_item_ids ?? [],
        can_accept: false,
        can_discard: false,
      }),
    ];
    return proposals[0];
  });
});
afterEach(() => {
  client.clear();
  vi.restoreAllMocks();
});

describe("room lead policy", () => {
  it("reads on opening without invoking the lead, and saves an explicit versioned policy", async () => {
    const { interaction } = await openPanel();
    expect(apiClient.post).not.toHaveBeenCalled();
    expect(apiClient.put).not.toHaveBeenCalled();
    await interaction.click(screen.getByRole("button", { name: "Configure lead" }));
    const dialog = screen.getByRole("dialog", { name: "Configure lead" });
    await interaction.click(within(dialog).getByRole("checkbox", { name: "Writer" }));
    await interaction.click(within(dialog).getByRole("checkbox", { name: "Analyst" }));
    await interaction.click(within(dialog).getByRole("button", { name: "Save lead" }));
    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith("/conversations/room-a/lead-policy", {
        expected_revision: 7,
        enabled: true,
        lead_agent_id: "writer",
        specialist_agent_ids: ["writer", "analyst"],
        max_items: 3,
      }),
    );
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("honors a read-only room even when a prior response allowed edits", async () => {
    await openPanel({ readOnly: true });
    expect(screen.queryByRole("button", { name: "Configure lead" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept selected" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discard proposal" })).not.toBeInTheDocument();
  });

  it("offers only published, runnable cloud agents", async () => {
    agents.push(
      agent("private", { can_run: false }),
      agent("draft", { status: "draft" }),
      agent("desktop", { local_workspace: true }),
      agent("subscription", { personal_coding_harness: "codex" }),
    );
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("button", { name: "Configure lead" }));
    const dialog = screen.getByRole("dialog");
    await interaction.click(within(dialog).getByRole("combobox", { name: "Lead agent" }));
    expect(screen.getByRole("option", { name: "Writer" })).toBeVisible();
    expect(screen.getByRole("option", { name: "Analyst" })).toBeVisible();
    for (const name of ["private", "draft", "desktop", "subscription"])
      expect(screen.queryByRole("option", { name })).not.toBeInTheDocument();
  });

  it("requires a reload after a policy conflict instead of overwriting it", async () => {
    vi.mocked(apiClient.put).mockRejectedValueOnce(new ApiError(409, "stale policy"));
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("button", { name: "Configure lead" }));
    await interaction.click(screen.getByRole("button", { name: "Save lead" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The room policy changed");
    expect(screen.queryByRole("button", { name: "Save lead" })).not.toBeInTheDocument();
    policy = { ...policy, revision: 8 };
    await interaction.click(screen.getByRole("button", { name: "Reload policy" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(apiClient.put).toHaveBeenCalledTimes(1);
  });

  it("shows read-only state without exposing policy or decision controls", async () => {
    policy = { ...policy, can_edit: false, can_ask: false };
    proposals = [proposal({ can_accept: false, can_discard: false })];
    await openPanel();
    expect(screen.queryByRole("button", { name: "Configure lead" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept selected" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discard proposal" })).not.toBeInTheDocument();
    expect(screen.getByText("The requester must review this proposal.")).toBeVisible();
    expect(apiClient.post).not.toHaveBeenCalled();
  });
});

describe("human proposal decisions", () => {
  it("renders model text as text and accepts only the reviewed selection", async () => {
    proposals = [
      proposal({
        items: [
          { ...proposal().items[0]!, title: "<script>untrusted()</script>" },
          { ...proposal().items[0]!, proposal_item_id: "item-b", title: "A separate task" },
        ],
      }),
    ];
    const { interaction } = await openPanel();
    expect(screen.getByText("<script>untrusted()</script>")).toBeVisible();
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByRole("button", { name: "Accept selected" })).toBeDisabled();
    await interaction.click(screen.getByRole("checkbox", { name: "<script>untrusted()</script>" }));
    expect(apiClient.post).not.toHaveBeenCalled();
    await interaction.click(screen.getByRole("button", { name: "Accept selected" }));
    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith(
        "/conversations/room-a/routing-proposals/proposal-a/accept",
        {
          expected_revision: 1,
          idempotency_key: expect.any(String),
          selected_item_ids: ["item-a"],
        },
      ),
    );
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });

  it("reconciles a lost acknowledgement before retrying the identical decision token", async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new TypeError("Lost acknowledgement"));
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("checkbox", { name: "Review the invoice batch" }));
    await interaction.click(screen.getByRole("button", { name: "Accept selected" }));
    const retry = await screen.findByRole("button", { name: "Check and retry" });
    const first = vi.mocked(apiClient.post).mock.calls[0]![1];
    expect(screen.getByRole("checkbox")).toBeDisabled();
    await interaction.click(retry);
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.post).mock.calls[1]![1]).toEqual(first);
    const readIndex = vi
      .mocked(apiClient.get)
      .mock.calls.findIndex(([path]) => path.endsWith("/routing-proposals/proposal-a"));
    expect(readIndex).toBeGreaterThanOrEqual(0);
    expect(vi.mocked(apiClient.get).mock.invocationCallOrder[readIndex]).toBeLessThan(
      vi.mocked(apiClient.post).mock.invocationCallOrder[1]!,
    );
  });

  it("uses the committed decision after a lost acknowledgement without another POST", async () => {
    vi.mocked(apiClient.post).mockImplementationOnce(async () => {
      proposals = [
        proposal({
          decision: "accepted",
          revision: 2,
          accepted_item_ids: ["item-a"],
          can_accept: false,
          can_discard: false,
        }),
      ];
      throw new TypeError("Lost acknowledgement");
    });
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("checkbox", { name: "Review the invoice batch" }));
    await interaction.click(screen.getByRole("button", { name: "Accept selected" }));
    await interaction.click(await screen.findByRole("button", { name: "Check and retry" }));
    expect(await screen.findByText("Accepted as a task")).toBeVisible();
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Check and retry" })).not.toBeInTheDocument();
  });

  it("refuses an uncertain replay when current decision authority was revoked", async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new TypeError("Lost acknowledgement"));
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("checkbox", { name: "Review the invoice batch" }));
    await interaction.click(screen.getByRole("button", { name: "Accept selected" }));
    const retry = await screen.findByRole("button", { name: "Check and retry" });
    proposals = [proposal({ can_accept: false, can_discard: false })];
    await interaction.click(retry);
    expect(await screen.findByText("The requester must review this proposal.")).toBeVisible();
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Accept selected" })).not.toBeInTheDocument();
  });

  it("keeps the uncertain decision token through a refused read and recovery", async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new TypeError("Lost acknowledgement"));
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("checkbox", { name: "Review the invoice batch" }));
    await interaction.click(screen.getByRole("button", { name: "Accept selected" }));
    await screen.findByRole("button", { name: "Check and retry" });
    const first = vi.mocked(apiClient.post).mock.calls[0]![1];
    const prior = vi.mocked(apiClient.get).getMockImplementation()!;
    vi.mocked(apiClient.get).mockImplementation(async (path, options) => {
      if (path.endsWith("/routing-proposals"))
        throw new ApiError(503, "Read temporarily unavailable");
      return prior(path, options);
    });
    act(changed);
    expect(
      await screen.findByText("Proposals could not be read with your current access."),
    ).toBeVisible();
    expect(screen.queryByText("Review the invoice batch")).not.toBeInTheDocument();
    vi.mocked(apiClient.get).mockImplementation(prior);
    await interaction.click(screen.getByRole("button", { name: "Check again" }));
    await interaction.click(await screen.findByRole("button", { name: "Check and retry" }));
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.post).mock.calls[1]![1]).toEqual(first);
  });

  it("requires a fresh selection after a proposal revision conflict", async () => {
    vi.mocked(apiClient.post).mockImplementationOnce(async () => {
      proposals = [proposal({ revision: 2 })];
      throw new ApiError(409, "Stale proposal revision");
    });
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("checkbox", { name: "Review the invoice batch" }));
    await interaction.click(screen.getByRole("button", { name: "Accept selected" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Review the current proposal before deciding again",
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Accept selected" })).toBeDisabled(),
    );
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    await interaction.click(screen.getByRole("checkbox"));
    await interaction.click(screen.getByRole("button", { name: "Accept selected" }));
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.post).mock.calls[1]![1]).toMatchObject({
      expected_revision: 2,
      selected_item_ids: ["item-a"],
    });
  });

  it("discards explicitly without creating selected task items", async () => {
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("button", { name: "Discard proposal" }));
    expect(await screen.findByText("Discarded", { exact: true })).toBeVisible();
    expect(apiClient.post).toHaveBeenCalledWith(
      "/conversations/room-a/routing-proposals/proposal-a/discard",
      { expected_revision: 1, idempotency_key: expect.any(String) },
    );
    expect(apiClient.post).toHaveBeenCalledTimes(1);
  });

  it("keeps one in-flight decision and ignores its late result after switching accounts", async () => {
    const pending = deferred<RoomRoutingProposal>();
    vi.mocked(apiClient.post).mockReturnValueOnce(pending.promise);
    const { interaction } = await openPanel();
    await interaction.click(screen.getByRole("checkbox", { name: "Review the invoice batch" }));
    await interaction.dblClick(screen.getByRole("button", { name: "Accept selected" }));
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    proposals = [];
    policy = { ...policy, can_edit: false };
    act(() => useAuthStore.setState({ user: user("other") }));
    await act(async () =>
      pending.resolve(
        proposal({ decision: "accepted", revision: 2, accepted_item_ids: ["item-a"] }),
      ),
    );
    await interaction.click(screen.getByRole("button", { name: /Lead & tasks/ }));
    expect(
      await screen.findByText("No proposals yet. Ask the lead to suggest work when you need it."),
    ).toBeVisible();
    expect(screen.queryByText("Accepted as a task")).not.toBeInTheDocument();
    expect(screen.queryByText("Review the invoice batch")).not.toBeInTheDocument();
  });
});

describe("canonical task reads and audience boundaries", () => {
  it("shows waiting for review and the existing board route without private work links or writes", async () => {
    tasks = [
      task(),
      task({
        id: "link-b",
        task_id: "task-b",
        title: "Teammate's task",
        status: "planned",
        accountable_user_id: "another-member",
        accountable_user_name: "Jordan",
      }),
    ];
    const { interaction } = await openPanel();
    expect(await screen.findByText("Waiting for review", { exact: true })).toBeVisible();
    expect(screen.queryByText("Done", { exact: true })).not.toBeInTheDocument();
    expect(screen.getByText("Jordan", { exact: true })).toBeVisible();
    expect(
      screen.getByText(
        "This teammate starts their task through Tasks. Acceptance does not start it on their behalf.",
      ),
    ).toBeVisible();
    for (const link of screen.getAllByRole("link", { name: "Open task board" }))
      expect(link).toHaveAttribute("href", "/en/tables/board-a");
    expect(screen.queryByRole("link", { name: /chat|Inbox|working/i })).not.toBeInTheDocument();
    act(changed);
    await waitFor(() =>
      expect(
        vi.mocked(apiClient.get).mock.calls.filter(([path]) => path.endsWith("/task-links")).length,
      ).toBeGreaterThan(1),
    );
    await interaction.click(screen.getByRole("button", { name: /Lead & tasks/ }));
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("hides previously loaded cards when the current read is refused", async () => {
    tasks = [task()];
    await openPanel();
    expect(await screen.findByText("Reconcile invoices")).toBeVisible();
    vi.mocked(apiClient.get).mockImplementation(async (path) => {
      if (path === "/orgs") return { items: [] };
      throw new ApiError(403, "Room access revoked");
    });
    act(changed);
    expect(
      await screen.findByText(
        "The room lead policy is unavailable. Check again to confirm your access.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Reconcile invoices")).not.toBeInTheDocument();
    expect(screen.queryByText("Review the invoice batch")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Configure lead" })).not.toBeInTheDocument();
  });

  it("hides all cached cards as soon as room access is refused while other reads are pending", async () => {
    tasks = [task()];
    await openPanel();
    expect(await screen.findByText("Reconcile invoices")).toBeVisible();
    const pending = deferred<{ items: never[]; has_more: boolean }>();
    vi.mocked(apiClient.get).mockImplementation(async (path) => {
      if (path === "/orgs") return { items: [] };
      if (path.endsWith("/lead-policy")) throw new ApiError(403, "Room access revoked");
      return pending.promise;
    });
    act(changed);
    expect(
      await screen.findByText(
        "The room lead policy is unavailable. Check again to confirm your access.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Reconcile invoices")).not.toBeInTheDocument();
    expect(screen.queryByText("Review the invoice batch")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Accept selected" })).not.toBeInTheDocument();
    await act(async () => pending.resolve({ items: [], has_more: false }));
  });

  it("separates organization reads and hides old cards while the next tenant is loading", async () => {
    tasks = [task()];
    const { interaction } = await openPanel();
    expect(await screen.findByText("Reconcile invoices")).toBeVisible();
    const pending = deferred<{ items: RoomTaskCard[]; has_more: boolean }>();
    const prior = vi.mocked(apiClient.get).getMockImplementation()!;
    vi.mocked(apiClient.get).mockImplementation((path, options) =>
      path.endsWith("/task-links") ? pending.promise : prior(path, options),
    );
    act(() => useOrgStore.setState({ activeOrgId: "org-b" }));
    await interaction.click(screen.getByRole("button", { name: /Lead & tasks/ }));
    expect(screen.queryByText("Reconcile invoices")).not.toBeInTheDocument();
    await act(async () =>
      pending.resolve({ items: [task({ title: "Second organization task" })], has_more: false }),
    );
    expect(await screen.findByText("Second organization task")).toBeVisible();
    expect(screen.queryByText("Reconcile invoices")).not.toBeInTheDocument();
  });

  it("uses explicit subthread reads and excludes foreign room or thread cards", async () => {
    proposals = [
      proposal({ thread_root_message_id: "thread-a" }),
      proposal({
        id: "foreign-room",
        conversation_id: "room-b",
        thread_root_message_id: "thread-a",
        items: [{ ...proposal().items[0]!, title: "Foreign room secret" }],
      }),
      proposal({
        id: "foreign-thread",
        thread_root_message_id: "thread-b",
        items: [{ ...proposal().items[0]!, title: "Other thread content" }],
      }),
    ];
    tasks = [
      task({ thread_root_message_id: "thread-a" }),
      task({ id: "other-thread", title: "Wrong thread task", thread_root_message_id: "thread-b" }),
    ];
    await openPanel({ threadRootMessageId: "thread-a" });
    expect(await screen.findByText("Reconcile invoices")).toBeVisible();
    expect(screen.queryByText("Foreign room secret")).not.toBeInTheDocument();
    expect(screen.queryByText("Other thread content")).not.toBeInTheDocument();
    expect(screen.queryByText("Wrong thread task")).not.toBeInTheDocument();
    expect(apiClient.get).toHaveBeenCalledWith("/conversations/room-a/routing-proposals", {
      params: { limit: "50", skip: "0", thread_root_message_id: "thread-a" },
      signal: expect.any(AbortSignal),
    });
    expect(apiClient.get).toHaveBeenCalledWith("/conversations/room-a/task-links", {
      params: { limit: "50", skip: "0", thread_root_message_id: "thread-a" },
      signal: expect.any(AbortSignal),
    });
  });

  it("loads another bounded page with the same thread scope only on request", async () => {
    const prior = vi.mocked(apiClient.get).getMockImplementation()!;
    vi.mocked(apiClient.get).mockImplementation(async (path, options) => {
      if (!path.endsWith("/routing-proposals")) return prior(path, options);
      const skip = (options?.params as Record<string, string>)?.skip;
      return skip === "0"
        ? { items: [proposal({ thread_root_message_id: "thread-a" })], has_more: true }
        : {
            items: [
              proposal({
                id: "next-proposal",
                thread_root_message_id: "thread-a",
                items: [{ ...proposal().items[0]!, title: "Next page task" }],
              }),
            ],
            has_more: false,
          };
    });
    const { interaction } = await openPanel({ threadRootMessageId: "thread-a" });
    expect(screen.queryByText("Next page task")).not.toBeInTheDocument();
    await interaction.click(screen.getByRole("button", { name: "Load more proposals" }));
    expect(await screen.findByText("Next page task")).toBeVisible();
    expect(apiClient.get).toHaveBeenCalledWith("/conversations/room-a/routing-proposals", {
      params: { limit: "50", skip: "50", thread_root_message_id: "thread-a" },
      signal: expect.any(AbortSignal),
    });
    expect(screen.queryByRole("button", { name: "Load more proposals" })).not.toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("opens the existing reply thread from a room-wide card without starting work", async () => {
    proposals = [proposal({ thread_root_message_id: "thread-a" })];
    const open = vi.fn();
    const { interaction } = await openPanel({ onOpenThread: open });
    await interaction.click(screen.getByRole("button", { name: "Open reply thread" }));
    expect(open).toHaveBeenCalledWith("thread-a");
    expect(apiClient.post).not.toHaveBeenCalled();
  });
});
