import { type NextRequest } from "next/server";
import { z } from "zod";

import { BFF_ERROR_KEYS, type BffErrorCode } from "@/lib/bff-errors";
import { bffJson, bffRefusal } from "@/lib/server-api";
import { ACCESS_TOKEN_COOKIE } from "@/lib/session-cookie";

const statusSchema = z.object({
  available: z.boolean(),
  running: z.boolean(),
  connected: z.boolean(),
  auth_method: z.string().max(64).nullable(),
  subscription_type: z.string().max(64).nullable(),
  session_mode: z.enum(["login", "session"]).nullable(),
});
const inputSchema = z.strictObject({
  action: z.enum(["login", "start", "stop", "sign_out", "ticket"]),
  cols: z.number().int().min(40).max(240).optional(),
  rows: z.number().int().min(10).max(80).optional(),
});
const resultSchema = z.object({
  status: statusSchema,
  ticket: z
    .string()
    .regex(/^[A-Za-z0-9_-]{32,256}$/)
    .optional(),
  expires_in: z.number().int().positive().max(120).optional(),
});
const MAX_BODY_BYTES = 1024;

/** Cap the streamed body too: Content-Length is not required on a request. */
async function readInput(request: NextRequest): Promise<unknown> {
  if (!request.body) return null;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return null;
  }
}

function allowedMutation(request: NextRequest, expectedOrigin: string): boolean {
  const site = request.headers.get("sec-fetch-site");
  return (
    request.headers.get("origin") === expectedOrigin &&
    request.headers.get("x-native-session") === "1" &&
    request.headers.get("content-type")?.split(";", 1)[0]?.trim() === "application/json" &&
    (site === null || site === "same-origin" || site === "none")
  );
}

function errorCode(body: unknown, status: number): BffErrorCode {
  if (status === 401) return "NOT_AUTHENTICATED";
  if (typeof body === "object" && body !== null && "code" in body) {
    const code = body.code;
    if (
      typeof code === "string" &&
      code.startsWith("NATIVE_") &&
      Object.hasOwn(BFF_ERROR_KEYS, code)
    )
      return code as BffErrorCode;
  }
  return status === 403 ? "NATIVE_ACCESS_DENIED" : "NATIVE_SESSION_FAILED";
}

/**
 * Forward only the signed-in browser's session and selected organisation.
 * The broker verifies both against the application API; owner IDs never arrive
 * from the browser. Native sign-in credentials never cross this HTTP endpoint.
 */
export async function nativeSubscriptionsProxy(request: NextRequest) {
  const accessToken = request.cookies.get(ACCESS_TOKEN_COOKIE)?.value;
  if (!accessToken) return bffRefusal("NOT_AUTHENTICATED", 401);
  const endpoint = process.env.NATIVE_SESSION_URL;
  const serviceKey = process.env.NATIVE_SESSION_SERVICE_KEY;
  if (!endpoint || !serviceKey) {
    return request.method === "GET"
      ? bffJson({
          available: false,
          running: false,
          connected: false,
          auth_method: null,
          subscription_type: null,
          session_mode: null,
        })
      : bffRefusal("NATIVE_UNAVAILABLE", 503);
  }
  const organization = z.uuid().safeParse(request.headers.get("x-organization-id"));
  if (!organization.success) return bffRefusal("NATIVE_INVALID_REQUEST", 400);

  try {
    const expectedOrigin = new URL(process.env.PUBLIC_SITE_URL || request.nextUrl.origin).origin;
    let body: string | undefined;
    let needsTicket = false;
    if (request.method === "POST") {
      if (!allowedMutation(request, expectedOrigin)) return bffRefusal("NATIVE_ACCESS_DENIED", 403);
      if (Number(request.headers.get("content-length")) > MAX_BODY_BYTES)
        return bffRefusal("NATIVE_INVALID_REQUEST", 413);
      const parsed = inputSchema.safeParse(await readInput(request));
      if (!parsed.success) return bffRefusal("NATIVE_INVALID_REQUEST", 400);
      body = JSON.stringify(parsed.data);
      needsTicket = ["login", "start", "ticket"].includes(parsed.data.action);
    }
    const response = await fetch(`${endpoint.replace(/\/$/, "")}/session`, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
        "X-Organization-Id": organization.data,
        "X-Native-Service-Key": serviceKey,
        Origin: expectedOrigin,
      },
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(30_000),
    });
    const data: unknown = await response.json().catch(() => null);
    if (!response.ok) return bffRefusal(errorCode(data, response.status), response.status);
    const parsed = (request.method === "GET" ? statusSchema : resultSchema).safeParse(data);
    if (!parsed.success) return bffRefusal("NATIVE_SESSION_FAILED", 502);
    if (needsTicket && (!("ticket" in parsed.data) || !parsed.data.ticket))
      return bffRefusal("NATIVE_SESSION_FAILED", 502);
    return bffJson(parsed.data);
  } catch {
    return bffRefusal("NATIVE_UNAVAILABLE", 503);
  }
}
