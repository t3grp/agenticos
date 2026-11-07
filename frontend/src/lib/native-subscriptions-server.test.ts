/** @vitest-environment node */
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET, POST } from "@/app/api/native-subscriptions/route";
import { ACCESS_TOKEN_COOKIE } from "@/lib/session-cookie";

const organization = "219d8268-832f-48c0-bad3-593592981aef";
const ticket = "test-ticket-012345678901234567890123456789";
const status = {
  available: true,
  running: false,
  connected: false,
  auth_method: null,
  subscription_type: null,
  session_mode: null,
};
const upstream = vi.fn<typeof fetch>();

function request(method = "GET", body?: unknown, headers: Record<string, string> = {}) {
  return new NextRequest("https://app.example/api/native-subscriptions", {
    method,
    headers: {
      cookie: `${ACCESS_TOKEN_COOKIE}=browser-access`,
      "x-organization-id": organization,
      "content-type": "application/json",
      "x-native-session": "1",
      origin: "https://app.example",
      "sec-fetch-site": "same-origin",
      ...headers,
    },
    ...(method === "POST" ? { body: JSON.stringify(body) } : {}),
  });
}

beforeEach(() => {
  vi.stubEnv("NATIVE_SESSION_URL", "http://native-service:8090");
  vi.stubEnv("NATIVE_SESSION_SERVICE_KEY", "private-service-key");
  vi.stubEnv("PUBLIC_SITE_URL", "https://app.example");
  vi.stubGlobal("fetch", upstream);
  upstream.mockReset();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("native session admission", () => {
  it("requires the namespaced app cookie before any broker request", async () => {
    const response = await GET(request("GET", undefined, { cookie: "unrelated_access=other" }));
    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(upstream).not.toHaveBeenCalled();
  });
  it.each(["NATIVE_SESSION_URL", "NATIVE_SESSION_SERVICE_KEY"])(
    "reports an unconfigured deployment without trying a fallback when %s is missing",
    async (name) => {
      vi.stubEnv(name, "");
      expect(await (await GET(request())).json()).toEqual({ ...status, available: false });
      expect((await POST(request("POST", { action: "start" }))).status).toBe(503);
      expect(upstream).not.toHaveBeenCalled();
    },
  );
  it.each(["", "another-user", "../../other"])(
    "refuses an invalid selected org %s",
    async (org) => {
      expect((await GET(request("GET", undefined, { "x-organization-id": org }))).status).toBe(400);
      expect(upstream).not.toHaveBeenCalled();
    },
  );
  it.each<Record<string, string>>([
    { origin: "https://evil.example" },
    { origin: "https://app.example.evil.example" },
    { origin: "null" },
    { origin: "" },
    { "sec-fetch-site": "cross-site" },
    { "sec-fetch-site": "same-site" },
    { "x-native-session": "" },
    { "content-type": "text/plain" },
  ])("refuses cross-origin or unconfirmed control requests: %j", async (headers) => {
    expect((await POST(request("POST", { action: "login" }, headers))).status).toBe(403);
    expect(upstream).not.toHaveBeenCalled();
  });
  it.each([
    { action: "start", user_id: "someone-else" },
    { action: "start", organization_id: "someone-else" },
    { action: "shell", command: "anything" },
    { action: "login", cols: 500 },
    { action: "login", rows: 0 },
    null,
    {},
  ])("refuses extra identity fields or invalid controls: %j", async (body) => {
    expect((await POST(request("POST", body))).status).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
  });
  it("bounds request bytes both with and without Content-Length", async () => {
    expect(
      (await POST(request("POST", { action: "start" }, { "content-length": "2048" }))).status,
    ).toBe(413);
    expect(
      (await POST(request("POST", { action: "start", padding: "x".repeat(2048) }))).status,
    ).toBe(400);
    expect(upstream).not.toHaveBeenCalled();
  });
  it("rejects a missing body and malformed JSON", async () => {
    const empty = request("POST", undefined);
    expect((await POST(empty)).status).toBe(400);
    const invalid = new NextRequest("https://app.example/api/native-subscriptions", {
      method: "POST",
      headers: request("POST", {}).headers,
      body: "{",
    });
    expect((await POST(invalid)).status).toBe(400);
  });
});

describe("native session proxy boundary", () => {
  it("forwards only server credentials and the selected org, and strips extra response fields", async () => {
    upstream.mockResolvedValue(
      new Response(JSON.stringify({ ...status, credential: "never-return" })),
    );
    const response = await GET(request());
    expect(await response.json()).toEqual(status);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const [url, init] = upstream.mock.calls[0]!;
    expect(url).toBe("http://native-service:8090/session");
    expect(init).toMatchObject({
      method: "GET",
      headers: {
        Authorization: "Bearer browser-access",
        "X-Organization-Id": organization,
        "X-Native-Service-Key": "private-service-key",
        Origin: "https://app.example",
      },
      cache: "no-store",
      redirect: "error",
    });
  });
  it("returns a one-use attachment ticket without accepting an owner or choosing a model", async () => {
    upstream.mockResolvedValue(new Response(JSON.stringify({ status, ticket, expires_in: 45 })));
    const response = await POST(request("POST", { action: "login", cols: 100, rows: 30 }));
    expect(await response.json()).toEqual({ status, ticket, expires_in: 45 });
    expect(JSON.parse(String(upstream.mock.calls[0]![1]!.body))).toEqual({
      action: "login",
      cols: 100,
      rows: 30,
    });
  });
  it("accepts stop and sign-out responses without a ticket", async () => {
    upstream.mockImplementation(async () => new Response(JSON.stringify({ status })));
    for (const action of ["stop", "sign_out"]) {
      expect(await (await POST(request("POST", { action }))).json()).toEqual({ status });
    }
  });
  it("rejects malformed success or a missing attachment ticket", async () => {
    upstream.mockResolvedValueOnce(new Response(JSON.stringify({ status })));
    expect((await POST(request("POST", { action: "start" }))).status).toBe(502);
    upstream.mockResolvedValueOnce(new Response(JSON.stringify({ status, ticket: "short" })));
    expect((await POST(request("POST", { action: "ticket" }))).status).toBe(502);
    upstream.mockResolvedValueOnce(new Response("not-json"));
    expect((await GET(request())).status).toBe(502);
  });
  it.each([
    [401, "NATIVE_AUTH_REQUIRED", "NOT_AUTHENTICATED"],
    [403, "UNKNOWN", "NATIVE_ACCESS_DENIED"],
    [429, "NATIVE_SESSION_LIMIT", "NATIVE_SESSION_LIMIT"],
    [500, "UNKNOWN", "NATIVE_SESSION_FAILED"],
  ])(
    "sanitizes the %s refusal while preserving its status",
    async (code, upstreamCode, expected) => {
      upstream.mockResolvedValue(
        new Response(JSON.stringify({ code: upstreamCode, detail: "private CLI output" }), {
          status: code as number,
        }),
      );
      const response = await GET(request());
      expect(response.status).toBe(code);
      expect(await response.json()).toEqual({ code: expected });
    },
  );
  it("handles a non-JSON refusal and an unreachable service without exposing errors", async () => {
    upstream.mockResolvedValueOnce(new Response("private stack", { status: 502 }));
    expect(await (await GET(request())).json()).toEqual({ code: "NATIVE_SESSION_FAILED" });
    upstream.mockRejectedValueOnce(new Error("private environment details"));
    expect(await (await GET(request())).json()).toEqual({ code: "NATIVE_UNAVAILABLE" });
  });
  it("uses the incoming origin only as the development default", async () => {
    vi.stubEnv("PUBLIC_SITE_URL", "");
    upstream.mockResolvedValue(new Response(JSON.stringify({ status, ticket })));
    expect((await POST(request("POST", { action: "start" }))).status).toBe(200);
  });
});
