/**
 * @vitest-environment node
 *
 * A server route: it runs where a browser global would be a lie.
 */
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const forward = vi.fn();

vi.mock("@/lib/platform-proxy", () => ({
  platformProxy: () => ({
    GET: forward,
    POST: forward,
    PUT: forward,
    PATCH: forward,
    DELETE: forward,
  }),
}));

const route = await import("./route");

beforeEach(() => {
  vi.clearAllMocks();
});

describe("the calendar proxy", () => {
  it.each(["GET", "POST", "PUT", "PATCH", "DELETE"] as const)(
    "forwards %s to the platform proxy",
    async (method) => {
      forward.mockResolvedValue(new Response(null, { status: 200 }));
      const request = new NextRequest("http://localhost:3000/api/calendar/events", {
        method,
      });
      await route[method](request);
      expect(forward).toHaveBeenCalledWith(request);
    },
  );
});
