/**
 * Where a provider's own billing lives — the invoice behind the estimate.
 *
 * The spend this product meters is priced from `genai-prices` snapshots, so
 * the only number that can close the loop is the provider's own. This table
 * holds the billing console for the providers that publish one at a stable
 * address; a provider with no entry here renders no link rather than a
 * guessed one, and the two kinds that never have a vendor bill say so
 * instead: a self-hosted server bills nobody, and a personal subscription
 * (Codex, Claude) is billed to the person, not to an API key.
 *
 * The ids are the provider ids `GET /providers/catalog` spells — see
 * `PROVIDERS` in `backend/app/agents/model_resolver.py`. A provider added
 * there without an entry here degrades to "no billing link on file", which a
 * test holds shut so the gap stays visible instead of silent.
 */
export type ProviderBilling =
  | { kind: "console"; href: string }
  | { kind: "self-hosted" }
  | { kind: "subscription" }
  | { kind: "unknown" };

const BILLING_CONSOLES: Readonly<Record<string, string>> = {
  openai: "https://platform.openai.com/settings/organization/billing/overview",
  anthropic: "https://console.anthropic.com/settings/billing",
  google: "https://console.cloud.google.com/billing",
  google_cloud: "https://console.cloud.google.com/billing",
  openrouter: "https://openrouter.ai/settings/credits",
  xai: "https://console.x.ai/team/billing",
  deepseek: "https://platform.deepseek.com/usage",
  groq: "https://console.groq.com/settings/billing",
  azure: "https://portal.azure.com/",
  bedrock: "https://console.aws.amazon.com/billing/",
  mistral: "https://console.mistral.ai/billing/",
  cohere: "https://dashboard.cohere.com/billing",
  together: "https://api.together.ai/settings/organization/~current/billing",
  fireworks: "https://app.fireworks.ai/",
  huggingface: "https://huggingface.co/settings/billing",
  github: "https://github.com/settings/billing",
  vercel: "https://vercel.com/account/billing",
  heroku: "https://dashboard.heroku.com/account/billing",
};

/** A model server on the deployment's own network: no vendor, no bill. */
const SELF_HOSTED: ReadonlySet<string> = new Set(["ollama", "litellm"]);

/** Billed to one person's subscription, not to an API key. */
const SUBSCRIPTION: ReadonlySet<string> = new Set(["openai_codex", "claude-subscription"]);

export function providerBilling(provider: string | null): ProviderBilling {
  if (provider === null) return { kind: "unknown" };
  const href = BILLING_CONSOLES[provider];
  if (href !== undefined) return { kind: "console", href };
  if (SELF_HOSTED.has(provider)) return { kind: "self-hosted" };
  if (SUBSCRIPTION.has(provider)) return { kind: "subscription" };
  return { kind: "unknown" };
}
