import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OnboardingSetup } from "./onboarding-setup";
import { ROUTES } from "@/lib/constants";

vi.mock("@/components/branding/branding-provider", () => ({
  useBranding: () => ({ appName: "Northwind AI", logoUrl: null }),
}));
vi.mock("@/components/layout/auth-guard", () => ({
  AuthGuard: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("next-intl", () => ({
  useTranslations: () => {
    const messages: Record<string, string> = {
      description: "Set up your workspace at your own pace.",
      appsTitle: "Connect your apps",
      optional: "Optional",
      appsDescription: "Composio handles authorization, and each provider asks for your consent.",
      connectApps: "Connect apps",
      opensNewTab: "Opens in a new tab",
      skipHint: "You can connect apps later from Connectors → Apps.",
      continueToWorkspace: "Continue to workspace",
    };
    return (key: string, values?: { appName?: string }) =>
      key === "title" ? `Welcome to ${values?.appName ?? "the workspace"}` : (messages[key] ?? key);
  },
}));

afterEach(() => vi.restoreAllMocks());

describe("onboarding setup", () => {
  it("keeps app connection optional and lets the user continue without authorizing", () => {
    const open = vi.spyOn(window, "open");
    render(<OnboardingSetup />);

    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("main")).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("heading", { name: "Welcome to Northwind AI" })).toBeInTheDocument();
    expect(screen.getByText("Optional")).toBeInTheDocument();

    const connect = screen.getByRole("link", { name: "Connect apps" });
    expect(
      screen.getByText("Composio handles authorization, and each provider asks for your consent."),
    ).toBeInTheDocument();
    expect(connect).toHaveAttribute("href", ROUTES.CONNECTORS);
    expect(connect).toHaveAttribute("target", "_blank");
    expect(connect).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByText("Opens in a new tab")).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Continue to workspace" })).toHaveAttribute(
      "href",
      ROUTES.CHAT,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(open).not.toHaveBeenCalled();
  });
});
