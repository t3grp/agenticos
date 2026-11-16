import { NextIntlClientProvider } from "next-intl";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserCredentialBindingsField } from "./browser-credential-bindings-field";
import type { FieldCopy } from "./capability-field-copy";
import type { Secret } from "@/types/secrets";
import en from "../../../messages/en.json";

const mocks = vi.hoisted(() => ({
  userId: "member-1",
  useSecrets: vi.fn(),
}));

vi.mock("@/hooks", () => ({ useSecrets: mocks.useSecrets }));
vi.mock("@/stores", () => ({
  useAuthStore: (selector: (state: { user: { id: string } | null }) => unknown) =>
    selector({ user: { id: mocks.userId } }),
}));

const copy: FieldCopy = {
  title: "Saved browser credentials",
  description: "Select the credentials this agent may ask a person to fill.",
};

function secret(overrides: Partial<Secret> = {}): Secret {
  return {
    id: "shared-login",
    name: "Work login",
    description: null,
    kind: "browser_login",
    hint: "",
    usage_scope: "organization",
    visibility: "org",
    owner_user_id: null,
    ...overrides,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  );
}

function field({
  value = [],
  connection = "built_in",
  disabled = false,
  error,
  onChange = vi.fn(),
}: {
  value?: unknown;
  connection?: unknown;
  disabled?: boolean;
  error?: string;
  onChange?: (ids: string[]) => void;
} = {}) {
  return {
    onChange,
    ...render(
      <BrowserCredentialBindingsField
        idPrefix="browser_use"
        connection={connection}
        value={value}
        copy={copy}
        error={error}
        disabled={disabled}
        onChange={onChange}
      />,
      { wrapper },
    ),
  };
}

function setSecrets(secrets: Secret[], listError: unknown = null, isLoading = false) {
  mocks.useSecrets.mockReturnValue({ secrets, listError, isLoading });
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.userId = "member-1";
  setSecrets([]);
});

describe("BrowserCredentialBindingsField", () => {
  it("offers only owned personal and organization-visible browser records", async () => {
    setSecrets([
      secret({ id: "own-login", name: "Personal login", usage_scope: "personal", visibility: "private", owner_user_id: "member-1" }),
      secret({ id: "other-login", name: "Other member login", usage_scope: "personal", visibility: "private", owner_user_id: "member-2" }),
      secret({ id: "team-login", name: "Team login", usage_scope: "organization", visibility: "team" }),
      secret({ id: "shared-profile", name: "Shared profile", kind: "browser_profile" }),
      secret({ id: "api-key", name: "API key", kind: "api_key" }),
    ]);
    const user = userEvent.setup();
    const onChange = vi.fn();
    field({ onChange });

    expect(screen.getByRole("checkbox", { name: /Personal login/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Shared profile/ })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Other member login/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Team login/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /API key/ })).not.toBeInTheDocument();

    const shared = screen.getByRole("checkbox", { name: /Shared profile/ });
    await user.click(shared);
    expect(onChange).toHaveBeenLastCalledWith(["shared-profile"]);
    await user.click(shared);
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("shows and clears selections that are missing after an organization switch", async () => {
    setSecrets([secret({ id: "old-org-login", name: "Old organization login" })]);
    const user = userEvent.setup();
    const onChange = vi.fn();
    const view = field({ value: ["old-org-login"], onChange });
    expect(screen.getByRole("checkbox", { name: /Old organization login/ })).toBeChecked();

    setSecrets([secret({ id: "new-org-login", name: "New organization login" })]);
    view.rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <BrowserCredentialBindingsField
          idPrefix="browser_use"
          connection="built_in"
          value={["old-org-login"]}
          copy={copy}
          onChange={onChange}
        />
      </NextIntlClientProvider>,
    );

    expect(
      screen.getByText(/selected browser credential is not available in this organization/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove unavailable selections" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("shows loading, unreadable and empty states without exposing ids", () => {
    setSecrets([], null, true);
    const view = field({ value: ["secret-id-that-is-not-shown"] });
    expect(screen.getByRole("status")).toHaveTextContent("Loading saved browser credentials");
    expect(screen.queryByText("secret-id-that-is-not-shown")).not.toBeInTheDocument();

    setSecrets([], new Error("synthetic failure"));
    view.rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <BrowserCredentialBindingsField
          idPrefix="browser_use"
          connection="built_in"
          value={["secret-id-that-is-not-shown"]}
          copy={copy}
          onChange={vi.fn()}
        />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Saved browser credentials could not be loaded");
    expect(screen.queryByText("secret-id-that-is-not-shown")).not.toBeInTheDocument();

    setSecrets([]);
    view.rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <BrowserCredentialBindingsField
          idPrefix="browser_use"
          connection="built_in"
          value={[]}
          copy={copy}
          onChange={vi.fn()}
        />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("No eligible browser logins or profiles");
  });

  it("keeps existing picks clearable outside the built-in browser and caps new picks", async () => {
    const many = Array.from({ length: 51 }, (_, index) =>
      secret({ id: "credential-" + index, name: "Credential " + index }),
    );
    setSecrets(many);
    const user = userEvent.setup();
    const onChange = vi.fn();
    const view = field({
      connection: "proxy",
      value: ["credential-0"],
      onChange,
    });

    expect(screen.getByText("Saved browser credentials work only with the deployment's built-in browser.")).toBeInTheDocument();
    const selected = screen.getByRole("checkbox", { name: /Credential 0/ });
    expect(selected).toBeChecked();
    expect(selected).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: /Credential 1/ })).toBeDisabled();
    await user.click(selected);
    expect(onChange).toHaveBeenLastCalledWith([]);
    view.unmount();

    const selectedIds = many.slice(0, 50).map((entry) => entry.id);
    const limited = field({ value: selectedIds, onChange });
    expect(screen.getByRole("checkbox", { name: /Credential 50/ })).toBeDisabled();
    limited.rerender(
      <NextIntlClientProvider locale="en" messages={en}>
        <BrowserCredentialBindingsField
          idPrefix="browser_use"
          connection="built_in"
          value={["credential-0"]}
          copy={copy}
          disabled
          onChange={onChange}
        />
      </NextIntlClientProvider>,
    );
    expect(screen.getByRole("checkbox", { name: /Credential 0/ })).toBeDisabled();
  });

  it("shows server publish errors beside the credential selector", () => {
    setSecrets([secret()]);
    field({ error: "A selected browser credential is not available to this publisher." });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "A selected browser credential is not available to this publisher.",
    );
  });
});
