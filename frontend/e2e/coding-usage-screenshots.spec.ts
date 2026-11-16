import { expect, test } from "./fixtures";
import { AUTH_STATE } from "./helpers";
import type { CodingAccount, CodingAccountUsage } from "../src/lib/coding-accounts-api";

test.use({ storageState: AUTH_STATE });

/**
 * Visual fixtures on the real Profile page, with synthetic owner metadata.
 * These images prove current-source layout, not a live provider observation.
 * The regular Profile screenshots still exercise the real backend separately.
 */
for (const viewport of [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const) {
  test(`captures populated coding usage fixture at ${viewport.name} width`, async ({
    page,
  }, testInfo) => {
    testInfo.annotations.push({
      type: "visual-fixture",
      description: "Synthetic owned account reports; no provider refresh or model execution.",
    });
    const now = Date.now();
    const observedAt = new Date(now - 12_000).toISOString();
    const validUntil = new Date(now + 300_000).toISOString();
    const firstReset = new Date(now + 2 * 60 * 60_000).toISOString();
    const secondReset = new Date(now + 4 * 24 * 60 * 60_000).toISOString();
    const codex: CodingAccount = {
      id: "visual-codex",
      harness: "codex",
      label: "Review Codex account",
      secret_kind: "openai_codex",
      enabled: true,
      generation: "visual-generation",
      is_default: true,
      pool_member: true,
      usage_refresh_pending: false,
      usage: {
        status: "available",
        source: "native_runtime",
        observed_at: observedAt,
        observation_age_seconds: 12,
        valid_until: validUntil,
        reason_code: null,
        windows: [
          {
            name: "primary",
            used: 37,
            limit: 100,
            unit: "percent",
            resets_at: firstReset,
            window_duration_minutes: 300,
          },
          {
            name: "secondary",
            used: 74,
            limit: 100,
            unit: "percent",
            resets_at: secondReset,
            window_duration_minutes: 10080,
          },
        ],
      },
      model_catalog: {
        status: "advertised",
        source: "native_runtime",
        observed_at: observedAt,
        valid_until: validUntil,
        model_ids: ["review-model-alpha", "review-model-beta"],
        reason_code: null,
      },
    };
    const claude: CodingAccount = {
      ...codex,
      id: "visual-claude",
      harness: "claude_code",
      label: "Review Claude account",
      secret_kind: "api_key",
      pool_member: false,
      usage: {
        status: "unknown",
        source: "unavailable",
        observed_at: null,
        observation_age_seconds: null,
        valid_until: null,
        windows: [],
        reason_code: "telemetry_unsupported",
      },
      model_catalog: {
        status: "unknown",
        source: "unavailable",
        observed_at: null,
        valid_until: null,
        model_ids: [],
        reason_code: "model_catalog_unverified",
      },
    };
    const report: CodingAccountUsage = {
      account_id: codex.id,
      generation: codex.generation,
      usage: codex.usage,
      model_catalog: codex.model_catalog,
      refresh_pending: false,
    };
    const unexpected: string[] = [];
    await page.route("**/api/coding-accounts**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace(/\/$/, "");
      if (route.request().method() === "GET" && path === "/api/coding-accounts") {
        await route.fulfill({ json: { items: [claude, codex] } });
      } else if (
        route.request().method() === "GET" &&
        path === `/api/coding-accounts/${codex.id}/usage`
      ) {
        await route.fulfill({ json: report });
      } else {
        unexpected.push(`${route.request().method()} ${path}`);
        // No account mutation or provider refresh can escape this visual fixture.
        await route.fulfill({ status: 400, json: { detail: "Unexpected visual fixture request" } });
      }
    });
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/settings/profile");
    const section = page.locator('section:has(> header[data-tour="personal-coding-accounts"])');
    const usage = section.getByRole("region", { name: "Usage for Review Codex account" });
    await expect(usage.getByText("Reported status: Available", { exact: true })).toBeVisible();
    await expect(usage.getByText("Source: Native account report", { exact: true })).toHaveCount(2);
    await expect(usage.locator(`time[datetime="${observedAt}"]`)).toHaveCount(2);
    await expect(usage.locator(`time[datetime="${validUntil}"]`)).toHaveCount(2);
    await expect(usage.locator(`time[datetime="${firstReset}"]`)).toHaveCount(1);
    await expect(usage.locator(`time[datetime="${secondReset}"]`)).toHaveCount(1);
    await expect(usage.getByText("37 / 100 %", { exact: true })).toBeVisible();
    await expect(usage.getByText("review-model-alpha", { exact: true })).toBeVisible();
    await expect(usage.getByRole("button", { name: "Refresh usage", exact: true })).toBeEnabled();
    await expect(
      section.getByText("Usage reporting is not supported for this connection.", { exact: true }),
    ).toBeVisible();
    expect(unexpected).toEqual([]);
    await section.screenshot({
      path: testInfo.outputPath(`coding-usage-fixture-${viewport.name}.png`),
    });
  });
}
