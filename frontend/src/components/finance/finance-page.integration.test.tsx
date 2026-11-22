import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FinancePage } from "./finance-page";
import { apiClient } from "@/lib/api-client";
import { ApiError } from "@/lib/api-error";
import { useOrgStore } from "@/stores";
import type { Organization } from "@/types";
import type { CostSummary } from "@/types/runs";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api-client")>("@/lib/api-client");
  return {
    ...actual,
    apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  };
});

const ORG_ID = "org-1";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function org(overrides: Partial<Organization> = {}): Organization {
  return {
    id: ORG_ID,
    name: "Acme",
    slug: "acme",
    avatar_url: null,
    avatar_color: null,
    is_personal: false,
    owner_id: "u1",
    chat_may_waive_approvals: false,
    public_live_data: false,
    monthly_budget_usd: "100",
    speech_to_text_provider: null,
    speech_to_text_model: null,
    speech_to_text_secret_id: null,
    speech_to_text_language: null,
    dictation_max_seconds: 300,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  };
}

function spend(overrides: Partial<CostSummary> = {}): CostSummary {
  return {
    period_days: null,
    from_date: "2026-11-01",
    to_date: "2026-11-22",
    month_to_date_usd: "42.10",
    partial_run_count: 0,
    by_agent: [],
    by_provider: [
      { provider: "openai", cost_usd: "25.00", run_count: 10 },
      { provider: "anthropic", cost_usd: "12.00", run_count: 6 },
      { provider: "ollama", cost_usd: "5.10", run_count: 40 },
    ],
    by_key: [],
    by_day: [],
    cache_by_agent_model: [],
    ...overrides,
  };
}

const CATALOG = {
  items: [
    { id: "openai", name: "OpenAI" },
    { id: "anthropic", name: "Anthropic" },
    { id: "ollama", name: "Ollama" },
  ],
  total: 3,
};

/** What the page's reads answer, per test. */
let role: "owner" | "member" | "none" = "owner";
let spendAnswer: CostSummary | "throw" | undefined = spend();
let spendPending: ((value: CostSummary) => void) | null = null;
let orgsAnswer: Organization[] | "pending" | undefined = [org()];
let catalogAnswer: typeof CATALOG | undefined = CATALOG;

function serve() {
  vi.mocked(apiClient.get).mockImplementation(async (path: string) => {
    if (path === "/me/permissions") {
      return {
        organization_id: ORG_ID,
        role: role === "owner" ? "owner" : "member",
        is_app_admin: false,
        permissions:
          role === "owner"
            ? [
                { permission: "runs:view", scope: "all" },
                { permission: "budgets:manage", scope: "all" },
              ]
            : role === "member"
              ? [{ permission: "runs:view", scope: "all" }]
              : [],
      };
    }
    if (path === "/spend") {
      if (spendAnswer === "throw") throw new ApiError(500, "the read failed");
      if (spendPending !== null) {
        return new Promise<CostSummary>((resolve) => {
          spendPending = resolve;
        });
      }
      return spendAnswer;
    }
    if (path === "/orgs") {
      if (orgsAnswer === "pending") return new Promise(() => {});
      return orgsAnswer === undefined ? undefined : { items: orgsAnswer, total: orgsAnswer.length };
    }
    if (path === "/providers/catalog") return catalogAnswer;
    throw new Error(`unexpected GET ${path}`);
  });
}

describe("FinancePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    role = "owner";
    spendAnswer = spend();
    spendPending = null;
    orgsAnswer = [org()];
    catalogAnswer = CATALOG;
    useOrgStore.setState({ activeOrgId: ORG_ID });
    serve();
  });

  it("shows the month against the cap, per-provider spend with billing links, the caveat and the TCO checklist", async () => {
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("This month against the cap")).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByText("$42.10 of a $100.00 monthly cap — $57.90 left")).toBeInTheDocument();
    const openAi = screen.getByRole("link", { name: "Open OpenAI billing" });
    expect(openAi).toHaveAttribute(
      "href",
      "https://platform.openai.com/settings/organization/billing/overview",
    );
    expect(openAi).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Open Anthropic billing" })).toHaveAttribute(
      "href",
      "https://console.anthropic.com/settings/billing",
    );
    expect(screen.getByText("Runs on your own hardware — no vendor bill.")).toBeInTheDocument();
    expect(screen.getByText("Spend is not your bill")).toBeInTheDocument();
    expect(screen.getByText("What running this actually costs")).toBeInTheDocument();
    expect(screen.getByText("Hosting")).toBeInTheDocument();
    expect(screen.getByText("Models")).toBeInTheDocument();
    expect(screen.getByText("Operator time")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Change the cap on the Members page" }),
    ).toHaveAttribute("href", "/orgs/org-1/members");
  });

  it("a member without budgets:manage reads the cap note instead of the change link", async () => {
    role = "member";
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("This month against the cap")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Only an admin can change the cap. The cap editor lives on the Members page.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Change the cap on the Members page")).toBeNull();
  });

  it("without a cap it says so and offers to set one", async () => {
    orgsAnswer = [org({ monthly_budget_usd: null })];
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("No monthly cap set")).toBeInTheDocument();
    expect(screen.getByText("Spent this month: $42.10")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Set the cap on the Members page" })).toHaveAttribute(
      "href",
      "/orgs/org-1/members",
    );
  });

  it("a zero cap reads as no cap, not Infinity%", async () => {
    orgsAnswer = [org({ monthly_budget_usd: "0" })];
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("No monthly cap set")).toBeInTheDocument();
    expect(screen.queryByText("Infinity%")).toBeNull();
  });

  it("without runs:view it says whose decision the absence is", async () => {
    role = "none";
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("You can't see spend")).toBeInTheDocument();
    expect(screen.queryByText("This month against the cap")).toBeNull();
  });

  it("a failed spend read shows the refusal, not nothing spent", async () => {
    spendAnswer = "throw";
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("Spend could not be read")).toBeInTheDocument();
    expect(screen.getByText("the read failed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("an empty spend answer is a failed read, not nothing spent", async () => {
    spendAnswer = undefined;
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("Spend could not be read")).toBeInTheDocument();
  });

  it("unpriced runs are marked a floor", async () => {
    spendAnswer = spend({ partial_run_count: 2 });
    render(<FinancePage />, { wrapper });

    expect(
      await screen.findByText(
        "2 runs this month could not be priced, so their cost is a floor, not a total",
      ),
    ).toBeInTheDocument();
  });

  it("a provider with no billing link on file says so, and an unrecorded one stays", async () => {
    spendAnswer = spend({
      by_provider: [
        { provider: "nebius", cost_usd: "3.00", run_count: 2 },
        { provider: null, cost_usd: "1.00", run_count: 1 },
      ],
    });
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("nebius")).toBeInTheDocument();
    expect(screen.getByText("Not recorded")).toBeInTheDocument();
    expect(
      screen.getAllByText("No billing link on file — check the provider's own console."),
    ).toHaveLength(2);
  });

  it("a subscription provider is billed to a person, not a key", async () => {
    spendAnswer = spend({
      by_provider: [{ provider: "openai_codex", cost_usd: "7.00", run_count: 3 }],
    });
    render(<FinancePage />, { wrapper });

    expect(
      await screen.findByText("Billed to a personal subscription, not an API key."),
    ).toBeInTheDocument();
  });

  it("the cap falls back to the first organization when the active one is not listed", async () => {
    orgsAnswer = [org({ id: "org-2", monthly_budget_usd: "50" })];
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("84%")).toBeInTheDocument();
    expect(screen.getByText("$42.10 of a $50.00 monthly cap — $7.90 left")).toBeInTheDocument();
  });

  it("with no organizations the spend still shows and the cap reads as unset", async () => {
    orgsAnswer = [];
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("No monthly cap set")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();
  });

  it("a missing provider catalog still names providers by id", async () => {
    catalogAnswer = undefined;
    render(<FinancePage />, { wrapper });

    expect(await screen.findByText("openai")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open openai billing" })).toBeInTheDocument();
  });

  it("shows a placeholder while spend is still loading", async () => {
    spendPending = () => {};
    render(<FinancePage />, { wrapper });

    // Permissions resolve first, so the no-access state clears and the page
    // waits on spend rather than reporting nothing spent.
    await waitFor(() => expect(screen.queryByText("You can't see spend")).toBeNull());
    expect(document.querySelector(".animate-pulse")).not.toBeNull();
    await act(async () => {
      spendPending?.(spend());
    });
    expect(await screen.findByText("This month against the cap")).toBeInTheDocument();
  });

  it("shows a placeholder while the organizations are still loading", async () => {
    orgsAnswer = "pending";
    render(<FinancePage />, { wrapper });

    await waitFor(() => expect(screen.queryByText("You can't see spend")).toBeNull());
    expect(document.querySelector(".animate-pulse")).not.toBeNull();
  });
});
