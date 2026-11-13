/** @vitest-environment node */
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BackendApiError, backendFetch } from "@/lib/server-api";

import { POST } from "./route";

vi.mock("@/lib/server-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/server-api")>("@/lib/server-api");
  return { ...actual, backendFetch: vi.fn() };
});

function request() {
  return new NextRequest("http://localhost:3000/api/auth/mfa/verify", {
    method: "POST",
    headers: { "content-type": "application/json", "x-forwarded-for": "10.0.0.9" },
    body: JSON.stringify({ mfa_token: "challenge", code: "123456" }),
  });
}

beforeEach(() => vi.clearAllMocks());

describe("POST /api/auth/mfa/verify", () => {
  it("keeps the browser JSON session response and cookies", async () => {
    vi.mocked(backendFetch)
      .mockResolvedValueOnce({ access_token: "at", refresh_token: "rt" })
      .mockResolvedValueOnce({ id: "u-1" });

    const response = await POST(request());

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      user: { id: "u-1" },
      access_token: "at",
      return_to: null,
    });
    expect(response.headers.getSetCookie()).toHaveLength(2);
    expect(backendFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/auth/mfa/verify",
      expect.objectContaining({ method: "POST", redirect: "manual" }),
    );
  });

  it("returns the backend desktop callback for the client to open", async () => {
    const location = "agenticos://auth/callback?code=single-use&desktop_nonce=nonce";
    vi.mocked(backendFetch).mockRejectedValueOnce(
      new BackendApiError(307, "Temporary Redirect", null, new Headers({ location })),
    );

    const response = await POST(request());

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ desktop_callback_url: location });
    expect(response.headers.getSetCookie()).toEqual([]);
    expect(backendFetch).toHaveBeenCalledTimes(1);
  });

  it("does not pass through a non-AgenticOS redirect", async () => {
    vi.mocked(backendFetch).mockRejectedValueOnce(
      new BackendApiError(
        307,
        "Temporary Redirect",
        null,
        new Headers({ location: "https://example.com/callback?code=secret" }),
      ),
    );

    const response = await POST(request());

    expect(response.status).toBe(502);
    await expect(response.json()).resolves.toEqual({ code: "LOGIN_FAILED" });
  });
});
