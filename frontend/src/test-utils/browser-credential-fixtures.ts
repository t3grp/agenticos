import type { SecretKindInfo } from "@/types/secrets";

export const BROWSER_IDS = {
  owner: "11111111-1111-4111-8111-111111111111",
  org: "22222222-2222-4222-8222-222222222222",
  secret: "33333333-3333-4333-8333-333333333333",
  generation: "44444444-4444-4444-8444-444444444444",
  run: "55555555-5555-4555-8555-555555555555",
  plan: "66666666-6666-4666-8666-666666666666",
  document: "77777777-7777-4777-8777-777777777777",
  child: "88888888-8888-4888-8888-888888888888",
};

export const LOGIN: SecretKindInfo = {
  kind: "browser_login",
  name: "Browser login",
  description: "A saved login for an explicitly confirmed browser destination.",
  json_schema: {
    type: "object",
    properties: {
      kind: { type: "string", const: "browser_login" },
      allowed_origins: { type: "array", items: { type: "string" }, title: "Allowed origins" },
      username: { type: "string", format: "password", title: "Username" },
      password: { type: "string", format: "password", title: "Password" },
      totp: { type: "object", title: "TOTP settings" },
    },
    required: ["allowed_origins", "username", "password"],
  },
};

export const PROFILE: SecretKindInfo = {
  kind: "browser_profile",
  name: "Browser contact profile",
  description: "Selected contact fields for an explicitly confirmed browser destination.",
  json_schema: {
    type: "object",
    properties: {
      kind: { type: "string", const: "browser_profile" },
      allowed_origins: { type: "array", items: { type: "string" }, title: "Allowed origins" },
      fields: { type: "object", title: "Contact fields" },
    },
    required: ["allowed_origins", "fields"],
  },
};
