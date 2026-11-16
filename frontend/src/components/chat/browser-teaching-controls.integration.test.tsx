import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Browse } from "@/lib/browse";
import { DEFAULT_PUBLIC_CONFIG } from "@/lib/public-config";
import { PublicConfigProvider } from "@/components/public-config/public-config-provider";
import {
  discardBrowserTeaching,
  getBrowserTeaching,
  proposeBrowserTeaching,
  reviewBrowserTeaching,
  setBrowserTeaching,
  startBrowserTeaching,
  stopBrowserTeaching,
  type BrowserTeaching,
} from "@/lib/browser-teachings-api";
import { BrowserTeachingControls } from "./browser-teaching-controls";

let skillsEdit = true;
let activeUserId: string | undefined = "synthetic-owner";
let activeOrgId: string | undefined = "synthetic-org";

vi.mock("@/hooks/use-permissions", () => ({
  usePermissions: () => ({ can: () => skillsEdit }),
}));

vi.mock("@/stores", () => ({
  useAuthStore: Object.assign(
    (selector: (state: { user: { id: string } }) => unknown) =>
      selector({ user: { id: activeUserId! } }),
    { getState: () => ({ user: { id: activeUserId! } }) },
  ),
  useOrgStore: (selector: (state: { activeOrgId: string }) => unknown) =>
    selector({ activeOrgId: activeOrgId! }),
}));

vi.mock("@/lib/browser-teachings-api", () => ({
  discardBrowserTeaching: vi.fn(),
  getBrowserTeaching: vi.fn(),
  proposeBrowserTeaching: vi.fn(),
  reviewBrowserTeaching: vi.fn(),
  setBrowserTeaching: vi.fn(),
  startBrowserTeaching: vi.fn(),
  stopBrowserTeaching: vi.fn(),
}));

const base = {
  id: "synthetic-teaching",
  agent_id: "synthetic-agent",
  run_id: "synthetic-run",
  call_id: "synthetic-call",
  lease_id: "synthetic-lease",
  origin: "https://portal.example.test",
  status: "recording",
  revision: 1,
  steps: [],
  parameters: [],
  proposal_id: null,
  pause_reason: null,
  expires_at: "2026-11-17T00:00:00Z",
};

const browse = {
  callId: "synthetic-call",
  runId: "synthetic-run",
  heldById: "synthetic-owner",
  nativeId: null,
} as Browse;

describe("managed browser teaching controls", () => {
  beforeEach(() => {
    skillsEdit = true;
    activeUserId = "synthetic-owner";
    activeOrgId = "synthetic-org";
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("demonstrates, pauses, reviews parameters and creates one proposal", async () => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue(base);
    vi.mocked(setBrowserTeaching)
      .mockResolvedValueOnce({ ...base, status: "paused", pause_reason: "manual", revision: 2 })
      .mockResolvedValueOnce({ ...base, revision: 3 });
    vi.mocked(stopBrowserTeaching).mockResolvedValue({
      ...base,
      status: "review",
      revision: 4,
      steps: [
        {
          event_id: "synthetic-event",
          sequence: 0,
          action: "input_parameter",
          origin: "https://portal.example.test",
          target_role: "textbox",
          target_fingerprint: "a".repeat(64),
          parameter: "input_1",
        },
        {
          event_id: "synthetic-click",
          sequence: 1,
          action: "click",
          origin: "https://portal.example.test",
          target_role: "button",
          target_fingerprint: "b".repeat(64),
        },
        {
          event_id: "synthetic-event-2",
          sequence: 2,
          action: "input_parameter",
          origin: "https://portal.example.test",
        },
        {
          event_id: "synthetic-click-2",
          sequence: 3,
          action: "click",
          origin: "https://portal.example.test",
        },
      ],
    });
    vi.mocked(reviewBrowserTeaching).mockImplementation(
      async (_id, _revision, _steps, parameters) => ({
        ...base,
        status: "review",
        revision: 5,
        steps: [
          {
            event_id: "synthetic-event",
            sequence: 0,
            action: "input_parameter",
            origin: "https://portal.example.test",
            parameter: parameters[0]?.name,
          },
        ],
        parameters,
      }),
    );
    vi.mocked(proposeBrowserTeaching).mockResolvedValue({
      ...base,
      status: "proposed",
      proposal_id: "synthetic-proposal",
    });

    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    expect(startBrowserTeaching).toHaveBeenCalledWith(
      "synthetic-run",
      "synthetic-call",
      "synthetic-lease",
      expect.any(String),
    );

    await user.click(screen.getByRole("button", { name: "Pause" }));
    await user.click(screen.getByRole("button", { name: "Resume" }));
    await user.click(screen.getByRole("button", { name: "Stop and review" }));
    expect(screen.getByText("input_parameter on page")).toBeVisible();
    await user.clear(screen.getByRole("textbox", { name: "Parameter 1 name" }));
    await user.type(screen.getByRole("textbox", { name: "Parameter 1 name" }), "report_month");
    await user.clear(screen.getByRole("textbox", { name: "Parameter 1 prompt label" }));
    await user.type(
      screen.getByRole("textbox", { name: "Parameter 1 prompt label" }),
      "Report month",
    );
    await user.type(screen.getByRole("textbox", { name: "Step 2 label" }), "Open invoices");
    await user.type(screen.getByRole("textbox", { name: "Step 4 label" }), "Export report");
    await user.type(screen.getByRole("textbox", { name: "Skill name" }), "Portal report");
    await user.click(screen.getByRole("button", { name: "Create skill proposal" }));

    expect(reviewBrowserTeaching).toHaveBeenCalledWith(
      "synthetic-teaching",
      4,
      [
        { event_id: "synthetic-click", reviewed_label: "Open invoices" },
        { event_id: "synthetic-click-2", reviewed_label: "Export report" },
      ],
      [
        { event_id: "synthetic-event", name: "report_month", label: "Report month" },
        { event_id: "synthetic-event-2", name: "input_2", label: "Parameter 2 label" },
      ],
    );
    expect(proposeBrowserTeaching).toHaveBeenCalledWith("synthetic-teaching", "Portal report", "");
    expect(await screen.findByText(/Proposal created/)).toBeVisible();
    expect(getBrowserTeaching).not.toHaveBeenCalled();
    expect(discardBrowserTeaching).not.toHaveBeenCalled();
  });

  it("does not show teaching to a caller without skill edit permission", () => {
    skillsEdit = false;
    render(<BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />);
    expect(screen.queryByRole("button", { name: "Teach a task" })).not.toBeInTheDocument();
  });

  it("does not show a start action without this tab's active lease", () => {
    render(<BrowserTeachingControls browse={browse} canControl leaseId={null} />);
    expect(screen.queryByRole("button", { name: "Teach a task" })).not.toBeInTheDocument();
  });

  it("keeps teaching controls hidden when the deployment flag is off", () => {
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: false }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );
    expect(screen.queryByRole("button", { name: "Teach a task" })).not.toBeInTheDocument();
  });

  it("clears the teaching session when the active organization changes", async () => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue(base);
    const ui = () => (
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>
    );
    const view = render(ui());

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    expect(await screen.findByRole("button", { name: "Pause" })).toBeVisible();

    activeOrgId = "synthetic-other-org";
    view.rerender(ui());

    expect(screen.queryByRole("button", { name: "Pause" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Teach a task" })).toBeVisible();
  });

  it.each([
    ["a browse without a run", { ...browse, runId: null }, true],
    ["a native browser", { ...browse, nativeId: "synthetic-native-tab" }, true],
    ["a browser the caller cannot control", browse, false],
    ["a browser held by another member", { ...browse, heldById: "another-user" }, true],
  ])("hides start for %s", (_reason, currentBrowse, canControl) => {
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls
          browse={currentBrowse as Browse}
          canControl={canControl}
          leaseId="synthetic-lease"
        />
      </PublicConfigProvider>,
    );

    expect(screen.queryByRole("button", { name: "Teach a task" })).not.toBeInTheDocument();
  });

  it("does not expose teaching when user or organization identity is missing", () => {
    activeUserId = undefined;
    activeOrgId = undefined;
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    expect(screen.queryByRole("button", { name: "Teach a task" })).not.toBeInTheDocument();
  });

  it("starts a new session after the previous browser lease ends", async () => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching)
      .mockResolvedValueOnce({ ...base, status: "paused", lease_id: "old-lease" })
      .mockResolvedValueOnce({ ...base, status: "recording" });
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    expect(screen.getByText(/Browser control ended/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Start a new teaching session" }));

    expect(startBrowserTeaching).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("button", { name: "Pause" })).toBeVisible();
  });

  it("pauses controls when the deployment flag is disabled during capture", async () => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue({ ...base, status: "paused" });
    const ui = (enabled: boolean) => (
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: enabled }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>
    );
    const view = render(ui(true));

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    view.rerender(ui(false));

    expect(
      screen.getByText("Browser teaching is disabled for this deployment.", { selector: "span" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();
  });

  it("does not offer proposal creation after the deployment flag is disabled", async () => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue({ ...base, status: "review" });
    const ui = (enabled: boolean) => (
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: enabled }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>
    );
    const view = render(ui(true));

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    view.rerender(ui(false));

    expect(
      screen.getByText("Browser teaching is disabled for this deployment.", { selector: "p" }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Create skill proposal" })).not.toBeInTheDocument();
  });

  it.each([
    ["event_limit", "Capture paused at the 500-step limit."],
    ["size_limit", "Capture paused at the 128 KiB limit."],
    ["duration_limit", "Capture paused at the 30-minute limit."],
    ["lease_ended", "Browser control ended."],
    ["lease_lost", "Browser control ended."],
    ["browser_finished", "The browser run ended."],
    ["origin_changed", "The browser left the taught site."],
    ["feature_disabled", "Browser teaching is disabled for this deployment."],
    ["unknown_reason", "Paused"],
  ])("shows a safe message for pause reason %s", async (pauseReason, message) => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue({
      ...base,
      status: "paused",
      pause_reason: pauseReason,
    });
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Teach a task" }));

    expect(screen.getByText(new RegExp(message), { selector: "p" })).toBeVisible();
  });

  it("clears an expired session without making another request", async () => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue({ ...base, status: "expired" });
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    await user.click(screen.getByRole("button", { name: "Start a new teaching session" }));

    expect(startBrowserTeaching).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Teach a task" })).toBeVisible();
  });

  it.each(["recording", "review"] as const)("discards a %s session", async (status) => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue({ ...base, status });
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    await user.click(screen.getByRole("button", { name: "Discard" }));

    expect(discardBrowserTeaching).toHaveBeenCalledWith("synthetic-teaching");
    expect(screen.getByRole("button", { name: "Teach a task" })).toBeVisible();
  });

  it("shows a safe error when a teaching action fails", async () => {
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockRejectedValue(new Error("synthetic failure"));
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Teach a task" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The teaching action failed. Reload the browser panel and try again.",
    );
  });

  it("refreshes recording state from the server", async () => {
    let poll: (() => void) | undefined;
    vi.spyOn(window, "setInterval").mockImplementation((handler) => {
      poll = handler as () => void;
      return {} as ReturnType<typeof window.setInterval>;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue(base);
    vi.mocked(getBrowserTeaching).mockResolvedValue({
      ...base,
      status: "paused",
      pause_reason: "manual",
      revision: 2,
    });
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    await act(async () => {
      poll?.();
      await Promise.resolve();
    });

    expect(getBrowserTeaching).toHaveBeenCalledWith("synthetic-teaching");
    expect(screen.getByRole("button", { name: "Resume" })).toBeVisible();
  });

  it("shows the polling error without exposing server details", async () => {
    let poll: (() => void) | undefined;
    vi.spyOn(window, "setInterval").mockImplementation((handler) => {
      poll = handler as () => void;
      return {} as ReturnType<typeof window.setInterval>;
    });
    vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockResolvedValue(base);
    vi.mocked(getBrowserTeaching).mockRejectedValue(new Error("synthetic secret"));
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    await act(async () => {
      poll?.();
      await Promise.resolve();
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The teaching action failed. Reload the browser panel and try again.",
    );
    expect(screen.queryByText("synthetic secret")).not.toBeInTheDocument();
  });

  it.each(["success", "failure"] as const)(
    "ignores a %s polling result after the active member changes",
    async (result) => {
      let poll: (() => void) | undefined;
      vi.spyOn(window, "setInterval").mockImplementation((handler) => {
        poll = handler as () => void;
        return {} as ReturnType<typeof window.setInterval>;
      });
      vi.spyOn(window, "clearInterval").mockImplementation(() => undefined);
      const user = userEvent.setup();
      vi.mocked(startBrowserTeaching).mockResolvedValue(base);
      if (result === "success") {
        vi.mocked(getBrowserTeaching).mockResolvedValue({ ...base, status: "paused" });
      } else {
        vi.mocked(getBrowserTeaching).mockRejectedValue(new Error("synthetic secret"));
      }
      render(
        <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
          <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
        </PublicConfigProvider>,
      );

      await user.click(screen.getByRole("button", { name: "Teach a task" }));
      activeUserId = "another-user";
      await act(async () => {
        poll?.();
        await Promise.resolve();
      });

      expect(screen.getByRole("button", { name: "Pause" })).toBeVisible();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    },
  );

  it("does not show an action error after the active member changes", async () => {
    let rejectStart: ((reason?: unknown) => void) | undefined;
    const user = userEvent.setup();
    vi.mocked(startBrowserTeaching).mockReturnValue(
      new Promise<BrowserTeaching>((_, reject) => {
        rejectStart = reject;
      }),
    );
    render(
      <PublicConfigProvider config={{ ...DEFAULT_PUBLIC_CONFIG, browserTeachingEnabled: true }}>
        <BrowserTeachingControls browse={browse} canControl leaseId="synthetic-lease" />
      </PublicConfigProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Teach a task" }));
    activeUserId = "another-user";
    await act(async () => {
      rejectStart?.(new Error("synthetic secret"));
      await Promise.resolve();
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
