import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EvalCasesPanel } from "./eval-cases-panel";
import { EvalCaseForm } from "./eval-case-form";
import { ProposalsPanel } from "./proposals-panel";
import { ProposalReview } from "./proposal-review";
import { TryView } from "./try-view";
import type { EvalCaseRead, EvalTryRead, ProposalRead } from "@/lib/failure-learning-api";

vi.mock("@/hooks/use-failure-learning", () => ({
  useEvalTry: () => ({ try: null as EvalTryRead | null }),
}));
vi.mock("@/hooks/use-skills", () => ({
  useSkills: () => ({ skills: [] }),
}));
vi.mock("@/hooks/use-runs", () => ({
  useRunTranscript: () => ({
    transcript: {
      items: [
        { id: "m1", role: "user", content: "First question" },
        { id: "m2", role: "assistant", content: "First answer" },
        { id: "m3", role: "user", content: "The failing question" },
      ],
    },
  }),
}));

const evalCase = (overrides: Partial<EvalCaseRead> = {}): EvalCaseRead => ({
  id: "case-1",
  agent_id: "agent-1",
  ledger_entry_id: "entry-1",
  name: "Bad requests return a hint",
  position: 0,
  input: { message: "Ask for a refund", history: [] },
  expectation: { kind: "contains", field: "text", value: "refund" },
  model_profile_id: null,
  source_run_id: null,
  latest_try: null,
  created_by_user_id: null,
  created_at: "2026-11-09T00:00:00Z",
  updated_at: "2026-11-09T00:00:00Z",
  ...overrides,
});

const proposal = (overrides: Partial<ProposalRead> = {}): ProposalRead => ({
  id: "proposal-1",
  organization_id: "org-1",
  agent_id: "agent-1",
  ledger_entry_id: "entry-1",
  eval_try_id: null,
  target_type: "instructions",
  skill_id: null,
  old_text: "Never mention refunds.",
  new_text: "Mention refunds when the user asks.",
  rationale: "The agent refused a fair refund request.",
  base_agent_version_id: null,
  base_content_sha256: null,
  status: "draft",
  applied_version_id: null,
  eval_evidence: null,
  applied_version: null,
  eval_gated: true,
  created_by_user_id: null,
  decided_by_user_id: null,
  decided_at: null,
  created_at: "2026-11-09T00:00:00Z",
  updated_at: "2026-11-09T00:00:00Z",
  ...overrides,
});

const evaluation = (overrides: Partial<EvalTryRead> = {}): EvalTryRead => ({
  id: "try-1",
  agent_id: "agent-1",
  proposal_id: null,
  cases: [
    {
      eval_id: "case-1",
      name: "Bad requests return a hint",
      expectation: { kind: "contains", field: "text", value: "refund" },
      old_run_ids: ["run-old"],
      new_run_ids: ["run-new"],
      old_passed: false,
      new_passed: true,
      gate_passed: true,
      status: "passed",
      reason: null,
    },
  ],
  passed: 1,
  failed: 0,
  pending: 0,
  gate_passed: true,
  created_at: "2026-11-09T00:00:00Z",
  ...overrides,
});

describe("EvalCasesPanel", () => {
  it("lists cases with the latest try verdict", () => {
    render(
      <EvalCasesPanel
        agentId="agent-1"
        cases={[
          evalCase({
            latest_try: {
              try_id: "try-1",
              old_passed: false,
              new_passed: true,
              gate_passed: true,
              status: "passed",
            },
          }),
        ]}
        proposals={[]}
        isLoading={false}
        error={null}
        ledgerEntryId={null}
        canEdit={true}
        onCreate={vi.fn()}
        onDelete={vi.fn()}
        onStartTry={vi.fn().mockResolvedValue(evaluation())}
        onProposeFromTry={vi.fn()}
      />,
    );

    expect(screen.getByText("Bad requests return a hint")).toBeInTheDocument();
    expect(screen.getByText("Gate passed")).toBeInTheDocument();
  });

  it("runs a try over the checked cases with the override", async () => {
    const user = userEvent.setup();
    const onStartTry = vi.fn().mockResolvedValue(evaluation());
    render(
      <EvalCasesPanel
        agentId="agent-1"
        cases={[evalCase()]}
        proposals={[]}
        isLoading={false}
        error={null}
        ledgerEntryId={null}
        canEdit={true}
        onCreate={vi.fn()}
        onDelete={vi.fn()}
        onStartTry={onStartTry}
        onProposeFromTry={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "Bad requests return a hint" }));
    await user.type(screen.getByLabelText("Proposed text"), "Mention refunds when asked.");
    await user.click(screen.getByRole("button", { name: "Run try" }));

    expect(onStartTry).toHaveBeenCalledWith(
      expect.objectContaining({
        case_ids: ["case-1"],
        instructions_override: "Mention refunds when asked.",
        samples: 1,
      }),
    );
  });

  it("deletes a case", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(
      <EvalCasesPanel
        agentId="agent-1"
        cases={[evalCase()]}
        proposals={[]}
        isLoading={false}
        error={null}
        ledgerEntryId={null}
        canEdit={true}
        onCreate={vi.fn()}
        onDelete={onDelete}
        onStartTry={vi.fn().mockResolvedValue(evaluation())}
        onProposeFromTry={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Delete case" }));
    expect(onDelete).toHaveBeenCalledWith("case-1");
  });

  it("hides curation controls from a caller who may not edit", () => {
    render(
      <EvalCasesPanel
        agentId="agent-1"
        cases={[evalCase()]}
        proposals={[]}
        isLoading={false}
        error={null}
        ledgerEntryId={null}
        canEdit={false}
        onCreate={vi.fn()}
        onDelete={vi.fn()}
        onStartTry={vi.fn().mockResolvedValue(evaluation())}
        onProposeFromTry={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Delete case" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New eval case" })).not.toBeInTheDocument();
  });

  it("says when there are no cases", () => {
    render(
      <EvalCasesPanel
        agentId="agent-1"
        cases={[]}
        proposals={[]}
        isLoading={false}
        error={null}
        ledgerEntryId={null}
        canEdit={true}
        onCreate={vi.fn()}
        onDelete={vi.fn()}
        onStartTry={vi.fn().mockResolvedValue(evaluation())}
        onProposeFromTry={vi.fn()}
      />,
    );

    expect(screen.getByText("No eval cases yet.")).toBeInTheDocument();
  });
});

describe("EvalCaseForm", () => {
  it("pre-fills the message from the run's last user turn", async () => {
    const user = userEvent.setup();
    render(<EvalCaseForm ledgerEntryId="entry-1" onSubmit={vi.fn()} onCancel={vi.fn()} />);

    await user.type(screen.getByLabelText("Source run"), "run-1");
    await user.click(screen.getByRole("button", { name: "Load from run" }));

    expect(screen.getByLabelText("Message that triggered the failure")).toHaveValue(
      "The failing question",
    );
  });

  it("submits the case with its expectation", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<EvalCaseForm ledgerEntryId="entry-1" onSubmit={onSubmit} onCancel={vi.fn()} />);

    await user.type(screen.getByLabelText("Name"), "Refund hint");
    await user.type(
      screen.getByLabelText("Message that triggered the failure"),
      "Ask for a refund",
    );
    await user.type(screen.getByLabelText("Value"), "refund");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        ledger_entry_id: "entry-1",
        name: "Refund hint",
        expectation: { kind: "contains", field: "text", value: "refund" },
      }),
    );
  });
});

describe("TryView", () => {
  it("says a case does not reproduce when the old text already passes", () => {
    render(
      <TryView
        evaluation={evaluation({
          gate_passed: false,
          cases: [
            {
              eval_id: "case-1",
              name: "Bad requests return a hint",
              expectation: { kind: "contains", field: "text", value: "refund" },
              old_run_ids: ["run-old"],
              new_run_ids: ["run-new"],
              old_passed: true,
              new_passed: true,
              gate_passed: false,
              status: "failed",
              reason: "case does not reproduce - the old text already passes",
            },
          ],
        })}
      />,
    );

    expect(
      screen.getByText("Case does not reproduce - the old text already passes."),
    ).toBeInTheDocument();
  });

  it("says when runs are still going", () => {
    render(<TryView evaluation={evaluation({ pending: 2 })} />);

    expect(
      screen.getByText("Try running - verdicts land as each side finishes."),
    ).toBeInTheDocument();
  });
});

describe("ProposalReview", () => {
  it("shows the old and new text, and refuses an ungated instruction apply", () => {
    render(
      <ProposalReview
        proposal={proposal()}
        canApplyInstructions={true}
        canApplySkill={false}
        canReject={true}
        onApply={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    expect(screen.getByText(/Never mention refunds/)).toBeInTheDocument();
    expect(screen.getByText(/Mention refunds when the user asks/)).toBeInTheDocument();
    expect(
      screen.getByText(
        "No eval try linked. An instruction proposal needs a gate-passed try before it can be applied.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  });

  it("enables apply once the try has passed the gate", () => {
    render(
      <ProposalReview
        proposal={proposal({ eval_evidence: evaluation() })}
        canApplyInstructions={true}
        canApplySkill={false}
        canReject={true}
        onApply={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Apply" })).not.toBeDisabled();
  });

  it("flags a skill target ungated, and applies it without a gate", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(
      <ProposalReview
        proposal={proposal({ target_type: "skill", skill_id: "skill-1", eval_gated: false })}
        canApplyInstructions={false}
        canApplySkill={true}
        canReject={true}
        onApply={onApply}
        onReject={vi.fn()}
      />,
    );

    expect(screen.getByText(/Not eval-gated/)).toBeInTheDocument();
    expect(
      screen.getByText(/reaches every agent bound to the skill as soon as it is applied/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalled();
  });

  it("hides apply from a caller who may not publish or edit skills", () => {
    render(
      <ProposalReview
        proposal={proposal({ eval_evidence: evaluation() })}
        canApplyInstructions={false}
        canApplySkill={false}
        canReject={true}
        onApply={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
  });

  it("rejects with a reason", async () => {
    const user = userEvent.setup();
    const onReject = vi.fn();
    render(
      <ProposalReview
        proposal={proposal()}
        canApplyInstructions={true}
        canApplySkill={false}
        canReject={true}
        onApply={vi.fn()}
        onReject={onReject}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.type(screen.getByLabelText("Reason for rejecting"), "too broad");
    await user.click(screen.getByRole("button", { name: "Reject" }));

    expect(onReject).toHaveBeenCalledWith("too broad");
  });
});

describe("ProposalsPanel", () => {
  it("lists proposals, and opens the review on click", async () => {
    const user = userEvent.setup();
    render(
      <ProposalsPanel
        proposals={[proposal()]}
        isLoading={false}
        error={null}
        ledgerEntryId={null}
        canEdit={true}
        canApplyInstructions={true}
        canApplySkill={false}
        canReject={true}
        prefill={null}
        onCreate={vi.fn()}
        onApply={vi.fn()}
        onReject={vi.fn()}
        onPrefillConsumed={vi.fn()}
      />,
    );

    expect(screen.getByText("Mention refunds when the user asks.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review" }));
    expect(screen.getByText(/Never mention refunds/)).toBeInTheDocument();
  });

  it("opens the create form pre-filled from a passing try", () => {
    render(
      <ProposalsPanel
        proposals={[]}
        isLoading={false}
        error={null}
        ledgerEntryId={null}
        canEdit={true}
        canApplyInstructions={true}
        canApplySkill={false}
        canReject={true}
        prefill={{ evalTryId: "try-1", newText: "Mention refunds when asked." }}
        onCreate={vi.fn()}
        onApply={vi.fn()}
        onReject={vi.fn()}
        onPrefillConsumed={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Proposed text")).toHaveValue("Mention refunds when asked.");
  });
});
