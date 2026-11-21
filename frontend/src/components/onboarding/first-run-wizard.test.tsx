import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FirstRunWizard } from "./first-run-wizard";

const env = vi.hoisted(() => ({
  data: {
    organization_id: "00000000-0000-4000-8000-000000000001",
    organization_name: "Demo",
    goal: null as string | null,
    dismissed: false,
    completed: false,
    snoozed_until: null as string | null,
    can_prepare: false,
    can_configure_model: false,
    support_email: null as string | null,
    agents: [] as Array<{ id: string; name: string }>,
    setup_agent: null as { id: string; name: string } | null,
    latest_conversation_id: null as string | null,
    latest_completed_at: null as string | null,
    has_result: false,
    has_activity: false,
  },
  error: null as unknown,
  isLoading: false,
  waitingForScope: false,
  refetch: vi.fn(),
  saveMutateAsync: vi.fn(async () => ({})),
  savePending: false,
  catalogApps: [] as Array<{ key: string; toolkit: string; name: string }>,
  connections: [] as Array<{ id: string }>,
  connectApp: vi.fn(async () => ({ redirect_url: "https://example.com/oauth" })),
  push: vi.fn(),
  assign: vi.fn(),
}));

vi.mock("@/hooks/use-start", () => ({
  useStart: () => ({
    data: env.data,
    error: env.error,
    isLoading: env.isLoading,
    waitingForScope: env.waitingForScope,
    refetch: env.refetch,
    save: { mutateAsync: env.saveMutateAsync, isPending: env.savePending },
  }),
}));
vi.mock("@/stores", () => ({
  useAuthStore: (pick: (state: { user: { id: string } }) => unknown) =>
    pick({ user: { id: "user-1" } }),
  useOrgStore: (pick: (state: { activeOrgId: string }) => unknown) =>
    pick({ activeOrgId: "org-1" }),
}));
vi.mock("@/lib/locale-navigation", () => ({ useRouter: () => ({ push: env.push }) }));
vi.mock("@/lib/app-connections-api", () => ({
  getAppConnectionCatalog: async () => ({ apps: env.catalogApps }),
  listAppConnections: async () => env.connections,
  connectApp: (input: { toolkit: string; label?: string }) => env.connectApp(input),
}));
// The model-setup dialog is covered where it lives; here the only question is
// whether the wizard opens it when no runnable agent exists.
vi.mock("./start-setup", () => ({
  StartSetup: ({ open }: { open: boolean }) =>
    open ? <div data-testid="start-setup-dialog">setup</div> : null,
}));

import enMessages from "../../../messages/en.json";

vi.mock("next-intl", () => ({
  useTranslations: (namespace?: string) => {
    const section: unknown = namespace
      ? (enMessages as unknown as Record<string, unknown>)[namespace]
      : enMessages;
    return (key: string, params?: Record<string, unknown>) => {
      let value: unknown = section;
      for (const part of key.split(".")) {
        if (typeof value !== "object" || value === null || !(part in value)) {
          value = undefined;
          break;
        }
        value = (value as Record<string, unknown>)[part];
      }
      if (typeof value !== "string") return key;
      if (!params) return value;
      return Object.entries(params).reduce(
        (result, [name, replacement]) =>
          result.replace(new RegExp(`\\{${name}\\}`, "g"), String(replacement)),
        value,
      );
    };
  },
}));

const AGENT_ID = "00000000-0000-4000-8000-000000000002";

function renderWizard(initialStep?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FirstRunWizard initialStep={initialStep} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  env.data = {
    organization_id: "00000000-0000-4000-8000-000000000001",
    organization_name: "Demo",
    goal: null,
    dismissed: false,
    completed: false,
    snoozed_until: null,
    can_prepare: false,
    can_configure_model: false,
    support_email: null,
    agents: [],
    setup_agent: null,
    latest_conversation_id: null,
    latest_completed_at: null,
    has_result: false,
    has_activity: false,
  };
  env.error = null;
  env.isLoading = false;
  env.waitingForScope = false;
  env.refetch.mockClear();
  env.saveMutateAsync.mockClear();
  env.saveMutateAsync.mockImplementation(async () => ({}));
  env.savePending = false;
  env.catalogApps = [];
  env.connections = [];
  env.connectApp.mockClear();
  env.push.mockClear();
  env.assign.mockClear();
  Object.defineProperty(window, "location", {
    value: { assign: env.assign },
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FirstRunWizard", () => {
  it("shows a loading state while the onboarding state loads", () => {
    env.isLoading = true;
    renderWizard();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.queryByText("Your first run, guided")).not.toBeInTheDocument();
  });

  it("shows an error state with a retry when the onboarding state fails", async () => {
    env.error = new Error("offline");
    renderWizard();
    expect(screen.getByText("The first-run check did not load.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(env.refetch).toHaveBeenCalled();
  });

  it("connects an app from the catalog and follows the sign-in URL", async () => {
    env.catalogApps = [{ key: "slack", toolkit: "slack", name: "Slack" }];
    renderWizard();
    await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));
    expect(env.connectApp).toHaveBeenCalledWith({ toolkit: "slack" });
    await waitFor(() => expect(env.assign).toHaveBeenCalledWith("https://example.com/oauth"));
  });

  it("reports a connection that did not start instead of leaving the wizard", async () => {
    env.catalogApps = [{ key: "slack", toolkit: "slack", name: "Slack" }];
    env.connectApp.mockRejectedValueOnce(new Error("nope"));
    renderWizard();
    await userEvent.click(await screen.findByRole("button", { name: "Connect Slack" }));
    expect(
      await screen.findByText("That connection did not start. Try again, or skip this step."),
    ).toBeInTheDocument();
  });

  it("skips the connection step and picks a goal, then moves to the run step", async () => {
    env.data.agents = [{ id: AGENT_ID, name: "AgenticOS" }];
    renderWizard();
    await userEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Turn meeting notes into an action plan" }),
    );
    expect(env.saveMutateAsync).toHaveBeenCalledWith({ goal: "meeting" });
    expect(screen.getByRole("button", { name: "Start my first task" })).toBeInTheDocument();
  });

  it("reports a goal choice that was not saved", async () => {
    env.saveMutateAsync.mockRejectedValueOnce(new Error("nope"));
    renderWizard();
    await userEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Turn meeting notes into an action plan" }),
    );
    expect(await screen.findByText("That choice was not saved. Try again.")).toBeInTheDocument();
  });

  it("continues from the goal step when an app is already connected", async () => {
    env.connections = [{ id: "conn-1" }];
    renderWizard();
    expect(await screen.findByRole("heading", { name: "Pick a goal" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip for now" })).not.toBeInTheDocument();
  });

  it("starts the first task in chat and checks for the result from the result step", async () => {
    env.data.agents = [{ id: AGENT_ID, name: "AgenticOS" }];
    env.data.goal = "document";
    renderWizard();
    expect(screen.getByRole("button", { name: "Start my first task" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start my first task" }));
    expect(env.push).toHaveBeenCalledWith(expect.stringContaining(`/chat?with=${AGENT_ID}`));
    expect(env.push).toHaveBeenCalledWith(expect.stringContaining("example=document"));
    await userEvent.click(
      screen.getByRole("button", { name: "I have run it — check for my result" }),
    );
    expect(screen.getByText("Waiting for your first finished run…")).toBeInTheDocument();
    expect(screen.getByText("What a finished result looks like")).toBeInTheDocument();
  });

  it("opens model setup when no runnable agent exists and one may be prepared", async () => {
    env.data.can_prepare = true;
    env.data.goal = "meeting";
    renderWizard();
    await userEvent.click(screen.getByRole("button", { name: "Set up a model" }));
    expect(screen.getByTestId("start-setup-dialog")).toBeInTheDocument();
  });

  it("says plainly when no agent can run and nobody here may prepare one", async () => {
    env.data.goal = "meeting";
    renderWizard();
    expect(screen.getByText("No runnable agent yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set up a model" })).not.toBeInTheDocument();
  });

  it("finishes only once a real run exists, and only then", async () => {
    env.data.has_result = true;
    env.data.latest_conversation_id = "conv-1";
    renderWizard();
    expect(screen.getByText("Your first task finished.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See the result in chat" })).toHaveAttribute(
      "href",
      "/chat?id=conv-1",
    );
    await userEvent.click(screen.getByRole("button", { name: "Finish — my first result is in" }));
    expect(env.saveMutateAsync).toHaveBeenCalledWith({ completed: true });
    expect(env.push).toHaveBeenCalledWith("/chat?id=conv-1");
  });

  it("resumes at the result step when the URL asks for it", () => {
    renderWizard("result");
    expect(screen.getByText("Waiting for your first finished run…")).toBeInTheDocument();
  });

  it("snoozes the offer and exits to chat without forcing the flow", async () => {
    renderWizard();
    await userEvent.click(screen.getByRole("button", { name: "Remind me tomorrow" }));
    expect(env.saveMutateAsync).toHaveBeenCalledWith({ dismissed: true });
    expect(env.push).toHaveBeenCalledWith("/dashboard");
    await userEvent.click(screen.getByRole("button", { name: "Exit to chat" }));
    expect(env.push).toHaveBeenCalledWith("/chat");
  });

  it("links back to the team builder", () => {
    renderWizard();
    expect(screen.getByRole("link", { name: "Build a team of agents instead" })).toHaveAttribute(
      "href",
      "/onboarding",
    );
  });
});
