import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { RoomLeadPanel } from "./room-lead-panel";
import { TeamRoomResponder } from "./team-room-responder";
import type { Agent } from "@/types/agents";
import type { RoomRouting } from "@/types/room-routing";

const { routing } = vi.hoisted(() => ({
  routing: {
    data: undefined as RoomRouting | undefined,
    isError: false,
    error: null as unknown,
    save: vi.fn(),
    decide: vi.fn(),
    isCurrent: () => true,
  },
}));
vi.mock("@/hooks/use-room-routing", () => ({ useRoomRouting: () => routing }));
const agents: Agent[] = [
  {
    id: "specialist",
    name: "Researcher",
    slug: "researcher",
    status: "published",
    can_run: true,
    current_version_id: "version-1",
    visibility: "private",
    owner_user_id: "me",
    description: null,
  },
];
function data(): RoomRouting {
  return {
    policy: {
      revision: 2,
      enabled: true,
      lead: { agent_id: "specialist", version_id: "pinned-old" },
      specialists: [{ agent_id: "specialist", version_id: "pinned-old" }],
      max_items: 3,
      can_edit: false,
    },
    proposals: [
      {
        id: "proposal",
        source_submission_id: "source",
        thread_root_message_id: "thread",
        requester_id: "me",
        revision: 1,
        policy_revision: 2,
        status: "pending",
        can_decide: true,
        can_accept: true,
        items: [
          {
            item_id: "item",
            title: "Review batch",
            expected_result: "List mismatches",
            specialist: { agent_id: "specialist", version_id: "pinned-old" },
            accountable_user_id: "me",
            reviewer_user_id: "reviewer",
            due_at: "2026-11-20T00:00:00Z",
            existing_task_id: null,
            expected_task_revision: null,
          },
        ],
      },
    ],
    tasks: [
      {
        task_id: "task",
        reviewer_user_id: "reviewer",
        proposal_id: "older",
        item_id: "older-item",
        thread_root_message_id: "thread",
        title: "Earlier batch",
        status: "waiting_for_user",
        next_actor: "user",
        assignee_user_id: "reviewer",
        assignee_agent_id: "specialist",
        revision: 4,
      },
    ],
  };
}
beforeEach(() => {
  routing.data = data();
  routing.isError = false;
  routing.error = null;
  routing.save.mockReset();
  routing.decide.mockReset();
});

it("shows the reviewed item and canonical review status without private links", () => {
  render(<RoomLeadPanel room="room" root="thread" agents={agents} />);
  expect(screen.getByText("Review batch")).toBeVisible();
  expect(screen.getByText("List mismatches")).toBeVisible();
  expect(screen.getAllByText(/Reviewer: reviewer/)).toHaveLength(2);
  expect(screen.getByText("Waiting for review")).toBeVisible();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(screen.queryByText("Pinned lead")).not.toBeInTheDocument();
});
it("keeps the decision key when retrying a lost acknowledgement", async () => {
  routing.decide.mockRejectedValueOnce(new Error("Lost acknowledgement"));
  render(<RoomLeadPanel room="room" root="thread" agents={agents} />);
  fireEvent.click(screen.getByRole("button", { name: "Accept handoffs" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Accept handoffs" }));
  await waitFor(() => expect(routing.decide).toHaveBeenCalledTimes(2));
  expect(routing.decide.mock.calls[0]?.[2]).toBe(routing.decide.mock.calls[1]?.[2]);
  expect(routing.decide.mock.calls[1]?.[3]).toEqual(["item"]);
});
it("discards without selecting any task", async () => {
  render(<RoomLeadPanel room="room" root="thread" agents={agents} />);
  fireEvent.click(screen.getByRole("button", { name: "Discard" }));
  await waitFor(() =>
    expect(routing.decide).toHaveBeenCalledWith(
      expect.objectContaining({ id: "proposal" }),
      false,
      expect.any(String),
      [],
    ),
  );
});
it("hides acceptance from another reader", () => {
  routing.data!.proposals[0]!.can_decide = false;
  render(<RoomLeadPanel room="room" root="thread" agents={agents} />);
  expect(screen.queryByRole("button", { name: "Accept handoffs" })).not.toBeInTheDocument();
});
it("preserves pinned versions when saving policy", async () => {
  routing.data!.policy.can_edit = true;
  render(<RoomLeadPanel room="room" root={null} agents={agents} />);
  fireEvent.click(screen.getByText("Room lead", { selector: "summary" }));
  fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
  await waitFor(() =>
    expect(routing.save).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_revision: 2,
        lead: { agent_id: "specialist", version_id: "pinned-old" },
        specialists: [{ agent_id: "specialist", version_id: "pinned-old" }],
      }),
    ),
  );
});
it("shows a read error instead of an empty successful state", () => {
  routing.isError = true;
  routing.error = new Error("Synthetic failure");
  render(<RoomLeadPanel room="room" root={null} agents={agents} />);
  expect(screen.getByRole("alert")).toBeVisible();
});
it("offers Ask lead only for an enabled policy", async () => {
  const { unmount } = render(
    <TeamRoomResponder agents={agents} value={null} onChange={() => undefined} disabled={false} />,
  );
  fireEvent.click(screen.getByRole("combobox"));
  expect(screen.queryByRole("option", { name: "Ask lead" })).not.toBeInTheDocument();
  unmount();
  render(
    <TeamRoomResponder
      agents={agents}
      value={null}
      onChange={() => undefined}
      disabled={false}
      allowLead
    />,
  );
  fireEvent.click(screen.getByRole("combobox"));
  expect(await screen.findByRole("option", { name: "Ask lead" })).toBeVisible();
});

it("keeps discard but hides acceptance when current task authority is revoked", () => {
  routing.data!.proposals[0]!.can_accept = false;
  render(<RoomLeadPanel room="room" root="thread" agents={agents} />);
  expect(screen.queryByRole("button", { name: "Accept handoffs" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Discard" })).toBeVisible();
});
