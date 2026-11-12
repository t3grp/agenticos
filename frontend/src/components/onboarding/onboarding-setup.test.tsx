import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OnboardingSetup } from "./onboarding-setup";

vi.mock("@/components/branding/branding-provider", () => ({
  useBranding: () => ({ appName: "AgenticOS", logoUrl: null }),
}));
vi.mock("@/components/layout/auth-guard", () => ({
  AuthGuard: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));
import enMessages from "../../../messages/en.json";

vi.mock("next-intl", () => ({
  useTranslations: (namespace?: string) => {
    const section = namespace ? (enMessages as Record<string, any>)[namespace] : enMessages;
    return (key: string, params?: Record<string, any>) => {
      const parts = key.split(".");
      let val: any = section;
      for (const p of parts) {
        val = val?.[p];
      }
      if (typeof val === "string") {
        if (params) {
          return Object.entries(params).reduce(
            (acc, [k, v]) => acc.replace(new RegExp(`\\{${k}\\}`, "g"), String(v)),
            val
          );
        }
        return val;
      }
      return key;
    };
  },
}));

afterEach(() => vi.restoreAllMocks());

describe("OnboardingSetup", () => {
  it("renders the guided team builder inside an auth guard", () => {
    render(<OnboardingSetup />);
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByText("Step 1 of 4")).toBeInTheDocument();
    expect(screen.getByText("About you & your work")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /skip for now/i })).toBeInTheDocument();
  });
});
