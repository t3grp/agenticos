import { describe, expect, it } from "vitest";

import { providerBilling } from "./provider-billing";

describe("providerBilling", () => {
  it("links the providers that publish a billing console", () => {
    expect(providerBilling("openai")).toEqual({
      kind: "console",
      href: "https://platform.openai.com/settings/organization/billing/overview",
    });
    expect(providerBilling("anthropic")).toEqual({
      kind: "console",
      href: "https://console.anthropic.com/settings/billing",
    });
    expect(providerBilling("openrouter")).toEqual({
      kind: "console",
      href: "https://openrouter.ai/settings/credits",
    });
    expect(providerBilling("mistral")).toEqual({
      kind: "console",
      href: "https://console.mistral.ai/billing/",
    });
    expect(providerBilling("cohere")).toEqual({
      kind: "console",
      href: "https://dashboard.cohere.com/billing",
    });
    expect(providerBilling("google_cloud")).toEqual({
      kind: "console",
      href: "https://console.cloud.google.com/billing",
    });
  });

  it("says so where no vendor bill exists, instead of linking nowhere", () => {
    expect(providerBilling("ollama")).toEqual({ kind: "self-hosted" });
    expect(providerBilling("litellm")).toEqual({ kind: "self-hosted" });
    expect(providerBilling("openai_codex")).toEqual({ kind: "subscription" });
    expect(providerBilling("claude-subscription")).toEqual({ kind: "subscription" });
  });

  it("admits it has no link for a provider it does not know, and for an unrecorded one", () => {
    expect(providerBilling("nebius")).toEqual({ kind: "unknown" });
    expect(providerBilling("sambanova")).toEqual({ kind: "unknown" });
    expect(providerBilling(null)).toEqual({ kind: "unknown" });
  });
});
