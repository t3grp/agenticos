import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
vi.mock("@/hooks/use-report-start-step", () => ({ useReportStartStep: () => undefined }));
import { AccountAppearance } from "@/components/layout/account-appearance";
import { useThemeStore } from "@/stores/theme-store";
import { useAuthStore } from "@/stores/auth-store";
import { useOrgStore } from "@/stores/org-store";
import { useMotionStore } from "@/stores/motion-store";
import { StartPreferences } from "./start-preferences";

const api = vi.hoisted(() => ({
  read: vi.fn(),
  save: vi.fn(),
  saveLocale: vi.fn(),
  push: vi.fn(),
  error: vi.fn(),
}));
vi.mock("@/lib/account-preferences-api", () => ({
  readAccountPreferences: api.read,
  saveAccountAppearance: api.save,
}));
vi.mock("@/lib/account-locale-api", () => ({ saveAccountLocale: api.saveLocale }));
vi.mock("@/lib/locale-navigation", () => ({
  usePathname: () => "/onboarding",
  useRouter: () => ({ push: api.push }),
}));
vi.mock("sonner", () => ({ toast: { error: api.error } }));

beforeEach(() => {
  vi.clearAllMocks();
  useThemeStore.setState({ theme: "dark", look: "paper" });
  useAuthStore.setState({
    user: {
      id: "preferences-user",
      email: "one@example.com",
      is_active: true,
      created_at: "2026-11-01",
      locale: "en",
    },
  });
  useOrgStore.setState({ activeOrgId: "existing-workspace" });
  api.read.mockResolvedValue({ theme: null, look: null, locale: null });
  api.save.mockResolvedValue({ theme: null, look: null, locale: null });
  api.saveLocale.mockResolvedValue("pl");
  window.history.replaceState(null, "", "/onboarding?invite=test&variant=studio#intro");
});
function mount(onContinue = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AccountAppearance />
      <StartPreferences onContinue={onContinue} />
    </QueryClientProvider>,
  );
  return onContinue;
}
it("offers English only without changing stored preferences on mount", async () => {
  mount();
  // The fork ships English only, and has no stored appearance to read back:
  // merely visiting saves nothing.
  expect(await screen.findByRole("radio", { name: "English" })).toBeChecked();
  expect(screen.getAllByRole("radio")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Back" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "Open the app" })).toHaveAttribute("href", "/chat");
  expect(api.read).not.toHaveBeenCalled();
  expect(api.save).not.toHaveBeenCalled();
  expect(api.saveLocale).not.toHaveBeenCalled();
  expect(useThemeStore.getState()).toMatchObject({ look: "paper", theme: "dark" });
});
it("confirms English through the shared hook and preserves the invitation, variant and fragment", async () => {
  // The fork offers only English, so there is no other language to pick; the
  // language is confirmed by Continue, which saves through the shared hook
  // without navigating, leaving the invitation, variant and fragment in place.
  mount();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(api.saveLocale).toHaveBeenCalledWith("en");
  expect(api.push).not.toHaveBeenCalled();
  expect(window.location.search).toBe("?invite=test&variant=studio");
  expect(window.location.hash).toBe("#intro");
  expect(useAuthStore.getState().user?.locale).toBe("en");
  expect(useOrgStore.getState().activeOrgId).toBe("existing-workspace");
  expect(api.save).not.toHaveBeenCalled();
});
it("reveals five actual looks and three color modes with existing selections preserved", async () => {
  mount();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(screen.queryByRole("radio", { name: "English" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Make the app feel like yours." })).toHaveFocus();
  const looks = screen.getByRole("radiogroup", { name: "Style" });
  expect(within(looks).getAllByRole("radio")).toHaveLength(5);
  expect(within(looks).getByRole("radio", { name: /Paper/ })).toBeChecked();
  const themes = screen.getByRole("radiogroup", { name: "Theme" });
  expect(within(themes).getAllByRole("radio")).toHaveLength(3);
  expect(within(themes).getByRole("radio", { name: "Dark" })).toBeChecked();
  expect(api.save).not.toHaveBeenCalled();
});
it("keeps deliberate appearance edits local and keeps them on Back", async () => {
  mount();
  await screen.findByRole("button", { name: "Continue" });
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  await userEvent.click(screen.getByRole("radio", { name: /Graphite/ }));
  expect(useThemeStore.getState().look).toBe("graphite");
  await userEvent.click(screen.getByRole("radio", { name: "Light" }));
  expect(useThemeStore.getState().theme).toBe("light");
  expect(api.save).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(screen.getByRole("heading", { name: "Which language feels natural?" })).toHaveFocus();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(screen.getByRole("radio", { name: /Graphite/ })).toBeChecked();
  expect(screen.getByRole("radio", { name: "Light" })).toBeChecked();
  expect(api.save).not.toHaveBeenCalled();
});
it("calls the parent only after both questions, without completing onboarding or changing workspace", async () => {
  const onContinue = mount();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(onContinue).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Continue to setup" }));
  expect(onContinue).toHaveBeenCalledOnce();
  expect(api.save).not.toHaveBeenCalled();
  expect(api.saveLocale).toHaveBeenCalledWith("en");
  expect(useOrgStore.getState().activeOrgId).toBe("existing-workspace");
});

it("confirms the displayed language for an account with no locale without resetting the step", async () => {
  useAuthStore.setState((state) => ({ user: state.user ? { ...state.user, locale: null } : null }));
  mount();
  expect(api.saveLocale).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
  expect(api.saveLocale).toHaveBeenCalledWith("en");
  expect(useAuthStore.getState().user?.locale).toBe("en");
  expect(api.push).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
    "Make the app feel like yours.",
  );
});

const originalAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "animate");
afterEach(() => {
  if (originalAnimate) Object.defineProperty(HTMLElement.prototype, "animate", originalAnimate);
  else Reflect.deleteProperty(HTMLElement.prototype, "animate");
  document.documentElement.style.removeProperty("--duration-base");
  document.documentElement.style.removeProperty("--ease-out");
  useMotionStore.setState({ motion: "system" });
});
it.each(["full", "reduce"] as const)(
  "uses the shared %s motion contract only when changing steps",
  async (motion) => {
    const animate = vi.fn(() => ({ cancel: vi.fn() }));
    Object.defineProperty(HTMLElement.prototype, "animate", { configurable: true, value: animate });
    document.documentElement.style.setProperty("--duration-base", "220ms");
    document.documentElement.style.setProperty("--ease-out", "ease-out");
    useMotionStore.setState({ motion });
    mount();
    expect(animate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(animate).toHaveBeenCalledTimes(motion === "full" ? 1 : 0);
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(animate).toHaveBeenCalledTimes(motion === "full" ? 2 : 0);
  },
);
