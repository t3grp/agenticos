import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SecretFields, isSecretComplete, secretFieldNames, toSecretPayload } from "./secret-form";
import type { JsonSchema } from "@/types/agents";
import type { SecretKindInfo } from "@/types/secrets";

vi.mock("@/hooks", () => ({
  useAgents: () => ({
    agents: [{ id: "agent-1", name: "Support agent", kind: "agent", status: "published" }],
    isLoading: false,
    error: null,
  }),
}));

/** `AwsCredentialsSecret`, as `GET /secrets/kinds` really serves it. */
const AWS_SCHEMA: JsonSchema = {
  type: "object",
  title: "AwsCredentialsSecret",
  properties: {
    kind: { const: "aws_credentials", default: "aws_credentials", type: "string", title: "Kind" },
    aws_access_key_id: { type: "string", title: "Aws Access Key Id" },
    aws_secret_access_key: {
      type: "string",
      format: "password",
      title: "Aws Secret Access Key",
    },
    region_name: { type: "string", title: "Region Name", description: "e.g. us-east-1" },
    aws_session_token: {
      anyOf: [{ type: "string", format: "password" }, { type: "null" }],
      title: "Aws Session Token",
      description: "Only for temporary STS credentials",
    },
  },
  required: ["aws_access_key_id", "aws_secret_access_key", "region_name"],
};

const AWS: SecretKindInfo = {
  kind: "aws_credentials",
  name: "AWS credentials",
  description: "An access key id, its secret access key and a region.",
  json_schema: AWS_SCHEMA,
};

const BROWSER_LOGIN: SecretKindInfo = {
  kind: "browser_login",
  name: "Browser login",
  description: "A login for approved browser origins.",
  json_schema: {
    type: "object",
    properties: {
      kind: { const: "browser_login", type: "string" },
      username: { type: "string", format: "password", title: "Username" },
      password: { type: "string", format: "password", title: "Password" },
      allowed_origins: { type: "array", items: { type: "string" }, title: "Allowed origins" },
      totp: { type: "object", title: "TOTP" },
      account_policy: {
        type: "string",
        enum: ["personal", "named_agents"],
        default: "personal",
        title: "Account use",
      },
      authorized_agent_ids: {
        type: "array",
        items: { type: "string" },
        title: "Authorized agents",
      },
    },
    required: ["username", "password", "allowed_origins"],
  },
};

const filled = {
  aws_access_key_id: "AKIAEXAMPLE",
  aws_secret_access_key: "wJalrXUtnFEMI",
  region_name: "us-east-1",
};

describe("SecretFields", () => {
  it("renders the fields the kind declares, and nothing the server decides", () => {
    // `kind` is a const in every payload schema. A control for it could only
    // ever be wrong, and here it would be a text box next to the picker that
    // already chose the kind.
    render(<SecretFields info={AWS} value={{}} onChange={vi.fn()} idPrefix="secret" />);

    expect(screen.getByLabelText(/Aws Access Key Id/, { selector: "input" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Region Name/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Kind/)).not.toBeInTheDocument();
  });

  it("masks every field the schema marks as a secret, including the optional one", () => {
    // `aws_session_token` is `anyOf: [{format: password}, {type: null}]`. It is
    // the one field of the five kinds where missing the null branch would put a
    // real credential on screen in the clear.
    render(<SecretFields info={AWS} value={{}} onChange={vi.fn()} idPrefix="secret" />);

    expect(screen.getByLabelText(/Aws Secret Access Key/, { selector: "input" })).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.getByLabelText(/Aws Session Token/, { selector: "input" })).toHaveAttribute(
      "type",
      "password",
    );
    // The access key id is not confidential and is what identifies the pair in
    // the AWS console. Masking it would hide the half worth reading.
    expect(screen.getByLabelText(/Aws Access Key Id/, { selector: "input" })).not.toHaveAttribute(
      "type",
      "password",
    );
  });

  it("reports what was typed against the field it was typed into", async () => {
    const onChange = vi.fn();
    render(<SecretFields info={AWS} value={{}} onChange={onChange} idPrefix="secret" />);

    await userEvent.type(screen.getByLabelText(/Region Name/), "e");
    expect(onChange).toHaveBeenLastCalledWith({ region_name: "e" });
  });

  it("shows what the server said about one field, beside that field", () => {
    // The refusal worth having here is the service account one: "its 'type' is
    // not service_account" is a sentence about one input, and a toast is
    // somewhere it cannot be acted on.
    render(
      <SecretFields
        info={AWS}
        value={filled}
        onChange={vi.fn()}
        idPrefix="secret"
        errors={{ region_name: "No such region" }}
      />,
    );

    expect(screen.getByText("No such region")).toBeInTheDocument();
    expect(screen.getByLabelText(/Region Name/)).toBeInvalid();
  });

  it("wires every label to the control it names", () => {
    // A generated form gets this wrong as easily as a hand-written one, and a
    // label with no htmlFor leaves the input it describes unnamed.
    const { container } = render(
      <SecretFields info={AWS} value={{}} onChange={vi.fn()} idPrefix="secret" />,
    );
    const labels = Array.from(container.querySelectorAll<HTMLLabelElement>("label"));

    expect(labels.length).toBeGreaterThan(0);
    for (const label of labels) {
      expect(label.htmlFor, `"${label.textContent}" names no control`).not.toBe("");
      expect(document.getElementById(label.htmlFor)).not.toBeNull();
    }
  });

  it("masks an optional TOTP seed and shows settings after a seed is entered", async () => {
    const onChange = vi.fn();
    render(<SecretFields info={BROWSER_LOGIN} value={{}} onChange={onChange} idPrefix="secret" />);

    const seed = screen.getByLabelText("TOTP seed (optional)", { selector: "input" });
    expect(seed).toHaveAttribute("type", "password");
    expect(screen.queryByRole("combobox", { name: "Algorithm" })).toBeNull();

    await userEvent.type(seed, "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ");

    expect(onChange).toHaveBeenLastCalledWith({
      totp: {
        seed: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        algorithm: "SHA1",
        digits: 6,
        period_seconds: 30,
      },
    });
    expect(screen.getByRole("combobox", { name: "Algorithm" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Code length" })).toBeVisible();
  });

  it("offers named agents by name and requires one for a shared account", async () => {
    const onChange = vi.fn();
    const browserValue = {
      username: "synthetic-user",
      password: "synthetic-password",
      allowed_origins: ["https://portal.example.test"],
      account_policy: "named_agents",
    };
    render(
      <SecretFields
        info={BROWSER_LOGIN}
        value={browserValue}
        onChange={onChange}
        idPrefix="secret"
      />,
    );

    expect(isSecretComplete(BROWSER_LOGIN.json_schema, browserValue, "browser_login")).toBe(false);
    await userEvent.click(screen.getByRole("checkbox", { name: "Support agent" }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...browserValue,
      authorized_agent_ids: ["agent-1"],
    });
    expect(
      isSecretComplete(
        BROWSER_LOGIN.json_schema,
        { ...browserValue, authorized_agent_ids: ["agent-1"] },
        "browser_login",
      ),
    ).toBe(true);
    expect(screen.queryByLabelText("Authorized agents", { selector: "input" })).toBeNull();
  });
});

describe("secretFieldNames", () => {
  it("lists what the form renders, so a refusal can be routed to it", () => {
    expect(secretFieldNames(AWS_SCHEMA)).toEqual([
      "aws_access_key_id",
      "aws_secret_access_key",
      "region_name",
      "aws_session_token",
    ]);
  });
});

describe("isSecretComplete", () => {
  it("accepts a payload with every required field answered", () => {
    expect(isSecretComplete(AWS_SCHEMA, filled)).toBe(true);
  });

  it("does not need the optional field", () => {
    // A session token is only for temporary STS credentials. Demanding one
    // would make the ordinary key pair unstorable.
    expect(isSecretComplete(AWS_SCHEMA, { ...filled, aws_session_token: undefined })).toBe(true);
  });

  it("refuses a missing required field", () => {
    expect(isSecretComplete(AWS_SCHEMA, { ...filled, region_name: undefined })).toBe(false);
  });

  it("refuses a required field holding only spaces", () => {
    // The backend's `min_length=1` counts a space, so this would be stored: a
    // credential that looks saved and authenticates nowhere.
    expect(isSecretComplete(AWS_SCHEMA, { ...filled, region_name: "   " })).toBe(false);
  });

  it("ignores the const the caller supplies", () => {
    // `kind` is required in the schema and never rendered. Counting it would
    // leave the submit button disabled on a complete form.
    expect(isSecretComplete({ ...AWS_SCHEMA, required: ["kind", "region_name"] }, filled)).toBe(
      true,
    );
  });
});

describe("toSecretPayload", () => {
  it("puts the discriminator on what was typed", () => {
    expect(toSecretPayload("api_key", { api_key: "sk-x" })).toEqual({
      kind: "api_key",
      api_key: "sk-x",
    });
  });

  it("is the caller's kind that wins, not one that came in with the values", () => {
    // Nothing can type a `kind` - the generated form never renders a const
    // field - but the payload is the one place a mismatch would be silent, and
    // the server would then refuse the whole credential for the wrong reason.
    expect(toSecretPayload("api_key", { kind: "aws_credentials" }).kind).toBe("api_key");
  });
});
