import { describe, expect, it } from "vitest";

import { nextConfig } from "./next.config";

describe("email font response headers", () => {
  it("allows cross-origin font loads only on the public email font path", async () => {
    const rules = await nextConfig.headers?.();
    const emailFontRule = rules?.find((rule) => rule.source === "/fonts/email/:path*");

    expect(emailFontRule?.headers).toEqual([{ key: "Access-Control-Allow-Origin", value: "*" }]);
    expect(
      rules
        ?.filter((rule) => rule.source !== "/fonts/email/:path*")
        .flatMap((rule) => rule.headers)
        .some((header) => header.key.toLowerCase() === "access-control-allow-origin"),
    ).toBe(false);
  });
});
