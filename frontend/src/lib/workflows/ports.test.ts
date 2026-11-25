import { describe, expect, it } from "vitest";

import { makeDefinition, port } from "@/components/workflows/validation/fixtures";

import {
  ERROR_PORT,
  WORKFLOW_ERROR_SCHEMA,
  effectiveDefinition,
  approvalChoices,
  errorBranches,
  instancePorts,
  splitBranches,
  switchRules,
  classifyCategories,
  routesErrors,
} from "./ports";
import type { NodeInstance } from "./types";

/**
 * The ports a node instance has once its config and policy are read - the client
 * mirror of the backend's `instance_ports`, so the canvas, the connection rule and
 * the validation mirror agree with what publishing checks.
 */

const HANDLED = { type: "object", title: "HandledError" };
const HANDLE = makeDefinition({
  id: "error.handle",
  ports: [port("in", "input", null), port("default", "output", HANDLED)],
});
const ACTION = makeDefinition({
  id: "data.map",
  ports: [port("in", "input", null), port("out", "output", null)],
});

function instance(overrides: Partial<NodeInstance>): NodeInstance {
  return {
    id: "n",
    definition_id: "data.map",
    definition_version: 1,
    config: {},
    layout: { x: 0, y: 0 },
    ...overrides,
  };
}

describe("instancePorts", () => {
  it("gives an error handler one output per configured branch, carrying the default's shape", () => {
    const handle = instance({
      definition_id: "error.handle",
      config: { branches: [{ name: "missing" }, { name: "default" }, { nope: 1 }, "junk"] },
    });
    const ports = instancePorts(handle, HANDLE);
    expect(ports.map((p) => p.id)).toEqual(["in", "default", "missing"]);
    expect(ports[2]?.schema).toBe(HANDLED);
  });

  it("gives a switch one output per rule, in order, before otherwise", () => {
    const OUT = { type: "object", title: "LogicSwitchOutput" };
    const SWITCH = makeDefinition({
      id: "logic.switch",
      ports: [port("in", "input", null), port("otherwise", "output", OUT)],
    });
    const configured = instance({
      definition_id: "logic.switch",
      config: { rules: [{ name: "poland" }, { name: "otherwise" }, { name: "germany" }] },
    });
    const ports = instancePorts(configured, SWITCH);
    expect(ports.map((p) => p.id)).toEqual(["in", "poland", "germany", "otherwise"]);
    expect(ports[1]?.schema).toBe(OUT);
    expect(switchRules({ rules: "junk" })).toEqual([]);
    const bare = makeDefinition({ id: "logic.switch", ports: [port("in", "input", null)] });
    expect(instancePorts(configured, bare).map((p) => p.id)).toEqual([
      "in",
      "poland",
      "otherwise",
      "germany",
    ]);
  });

  it("gives a Split one output per branch, taking any step", () => {
    const SPLIT = makeDefinition({ id: "logic.split", ports: [port("in", "input", null)] });
    const configured = instance({
      definition_id: "logic.split",
      config: { branches: [{ name: "a" }, { name: "in" }, { name: "b" }] },
    });
    const ports = instancePorts(configured, SPLIT);
    expect(ports.map((p) => [p.id, p.kind, p.schema])).toEqual([
      ["in", "input", null],
      ["a", "output", null],
      ["b", "output", null],
    ]);
    expect(splitBranches({ branches: "junk" })).toEqual([]);
  });

  it("gives a classifier one output per category, in order, before other", () => {
    const OUT = { type: "object", title: "AiClassifyOutput" };
    const CLASSIFY = makeDefinition({
      id: "ai.classify",
      ports: [port("in", "input", null), port("other", "output", OUT)],
    });
    const configured = instance({
      definition_id: "ai.classify",
      config: { categories: [{ name: "sales" }, { name: "support" }, { description: "nameless" }] },
    });

    const ports = instancePorts(configured, CLASSIFY);

    expect(ports.map((p) => p.id)).toEqual(["in", "sales", "support", "other"]);
    // The category port carries the answer typed by the categories: its
    // `category` is the enum of their names, and `other`.
    expect(ports[1]?.schema).toMatchObject({
      title: "AiClassifyOutput",
      properties: { category: { type: "string", enum: ["sales", "support", "other"] } },
    });
    expect(classifyCategories({})).toEqual([]);
  });

  describe("an approval's choices", () => {
    const OUT = { type: "object", title: "HumanApprovalOutput" };
    const APPROVAL = makeDefinition({
      id: "human.approval",
      ports: [
        port("in", "input", null),
        port("approved", "output", OUT),
        port("rejected", "output", OUT),
      ],
    });

    it("keeps approved and rejected when the step offers no choices", () => {
      const plain = instance({ definition_id: "human.approval", config: { title: "Send?" } });
      expect(instancePorts(plain, APPROVAL).map((p) => p.id)).toEqual([
        "in",
        "approved",
        "rejected",
      ]);
      expect(effectiveDefinition(plain, APPROVAL)).toBe(APPROVAL);
    });

    it("gives one output per choice, labelled as the author wrote it, in place of approved", () => {
      const asking = instance({
        definition_id: "human.approval",
        config: {
          choices: [
            { id: "draft", label: "Keep a draft" },
            { id: "send_now", label: "Send now" },
            { label: "no id" },
            "junk",
          ],
        },
      });
      const ports = instancePorts(asking, APPROVAL);
      expect(ports.map((p) => [p.id, p.label])).toEqual([
        ["in", "in"],
        ["draft", "Keep a draft"],
        ["send_now", "Send now"],
        ["rejected", "rejected"],
      ]);
      expect(ports[1]?.schema).toBe(OUT);
    });

    it("draws one handle per id, skipping a repeat and any id a port already has", () => {
      const asking = instance({
        definition_id: "human.approval",
        config: {
          choices: [
            { id: "draft", label: "Keep a draft" },
            { id: "draft", label: "Again" },
            { id: "in", label: "In" },
            { id: "rejected", label: "No" },
            { id: "approved", label: "Yes" },
            { id: "send_now", label: "Send now" },
          ],
        },
      });
      expect(instancePorts(asking, APPROVAL).map((p) => p.id)).toEqual([
        "in",
        "draft",
        "send_now",
        "rejected",
      ]);
    });

    it("reads a choice with no label as its id, and nothing from a config with no list", () => {
      expect(approvalChoices({ choices: [{ id: "draft" }] })).toEqual([
        { id: "draft", label: "draft" },
      ]);
      expect(approvalChoices({})).toEqual([]);
    });
  });

  describe("a classifier's unsure port", () => {
    const OUT = { type: "object", title: "AiClassifyOutput" };
    const CLASSIFY = makeDefinition({
      id: "ai.classify",
      ports: [port("in", "input", null), port("other", "output", OUT)],
    });
    const categories = [{ name: "sales" }, { name: "support" }];

    it("adds an unsure way out after other when the classifier has a minimum confidence", () => {
      const ports = instancePorts(
        instance({ definition_id: "ai.classify", config: { categories, min_confidence: 0.7 } }),
        CLASSIFY,
      );

      expect(ports.map((p) => p.id)).toEqual(["in", "sales", "support", "other", "unsure"]);
      expect(ports[4]).toMatchObject({
        id: "unsure",
        label: "Unsure",
        kind: "output",
        schema: {
          title: "AiClassifyOutput",
          properties: { category: { type: "string", enum: ["sales", "support", "other"] } },
        },
      });
      expect(ports[4]?.schema).toEqual(ports[3]?.schema);
    });

    it("draws the unsure port for a minimum of zero", () => {
      const ports = instancePorts(
        instance({ definition_id: "ai.classify", config: { categories, min_confidence: 0 } }),
        CLASSIFY,
      );
      expect(ports.map((p) => p.id)).toEqual(["in", "sales", "support", "other", "unsure"]);
    });

    it("draws one unsure handle when a category is called unsure too", () => {
      const ports = instancePorts(
        instance({
          definition_id: "ai.classify",
          config: { categories: [{ name: "unsure" }], min_confidence: 0.5 },
        }),
        CLASSIFY,
      );
      expect(ports.filter((p) => p.id === "unsure")).toHaveLength(1);
    });

    it("draws no unsure port without a minimum", () => {
      for (const config of [{ categories, min_confidence: null }, { categories }]) {
        const ports = instancePorts(instance({ definition_id: "ai.classify", config }), CLASSIFY);
        expect(ports.map((p) => p.id)).toEqual(["in", "sales", "support", "other"]);
      }
    });

    it("keeps a category called unsure as a category when there is no minimum", () => {
      const ports = instancePorts(
        instance({
          definition_id: "ai.classify",
          config: { categories: [{ name: "sales" }, { name: "unsure" }] },
        }),
        CLASSIFY,
      );

      expect(ports.map((p) => p.id)).toEqual(["in", "sales", "unsure", "other"]);
      expect(ports.filter((p) => p.id === "unsure")).toHaveLength(1);
    });
  });

  it("gives a step that routes its errors an error port carrying the WorkflowError", () => {
    const routed = instancePorts(instance({ policy: { on_error: "route" } }), ACTION);
    expect(routed.map((p) => p.id)).toEqual(["in", "out", ERROR_PORT]);
    expect(routed[2]?.schema).toBe(WORKFLOW_ERROR_SCHEMA);
  });

  it("adds nothing to a step that fails its run, and never a second error port", () => {
    expect(instancePorts(instance({}), ACTION)).toBe(ACTION.ports);
    const already = makeDefinition({ id: "x", ports: [port(ERROR_PORT, "output", null)] });
    expect(instancePorts(instance({ policy: { on_error: "route" } }), already)).toHaveLength(1);
  });
});

describe("the Manual or API trigger's ports", () => {
  const MANUAL = makeDefinition({
    id: "core.input",
    ports: [port("out", "output", { type: "object", properties: { payload: { type: "object" } } })],
  });

  it("types its payload by the fields it declares, and leaves it open with none", () => {
    expect(instancePorts(instance({ definition_id: "core.input" }), MANUAL)).toBe(MANUAL.ports);
    const typed = instancePorts(
      instance({
        definition_id: "core.input",
        config: { fields: [{ name: "email", type: "text" }] },
      }),
      MANUAL,
    );
    expect(typed[0]?.schema).toMatchObject({
      properties: { payload: { properties: { email: { type: "string" } }, required: ["email"] } },
    });
  });
});

describe("an event trigger's payload ports", () => {
  const outSchema = (attr: string) => ({
    type: "object",
    properties: { [attr]: { type: "object" }, delivery_id: { type: "string" } },
  });
  const trigger = (id: string, attr: string) =>
    makeDefinition({ id, ports: [port("out", "output", outSchema(attr))] });

  it.each([
    ["trigger.webhook", "body"],
    ["trigger.github", "payload"],
    ["trigger.schedule", "input"],
    ["trigger.table_record", "fields"],
  ])("types %s's %s by the Tier 1 fields it declares", (id, attr) => {
    const definition = trigger(id, attr);
    const bare = instance({ definition_id: id });
    expect(instancePorts(bare, definition)).toBe(definition.ports);
    const typed = instancePorts(
      instance({
        definition_id: id,
        config: { payload_fields: [{ name: "title", type: "text" }] },
      }),
      definition,
    );
    expect(typed[0]?.schema).toMatchObject({
      properties: {
        [attr]: { properties: { title: { type: "string" } }, required: ["title"] },
        delivery_id: { type: "string" },
      },
    });
  });

  it("types a payload from its Tier 2 schema as written, and leaves other attributes alone", () => {
    const schema = {
      type: "object",
      properties: { pull_request: { type: "object", properties: { title: { type: "string" } } } },
    };
    const typed = instancePorts(
      instance({
        definition_id: "trigger.github",
        config: { payload_schema: schema },
      }),
      trigger("trigger.github", "payload"),
    );
    expect(typed[0]?.schema).toMatchObject({ properties: { payload: schema } });
  });

  it("leaves a table record's values untyped: only fields, by label, is declared", () => {
    const definition = makeDefinition({
      id: "trigger.table_record",
      ports: [
        port("out", "output", {
          type: "object",
          properties: { fields: { type: "object" }, values: { type: "object" } },
        }),
      ],
    });
    const typed = instancePorts(
      instance({
        definition_id: "trigger.table_record",
        config: { payload_fields: [{ name: "Email", type: "text" }] },
      }),
      definition,
    );
    const properties = (typed[0]?.schema as { properties: Record<string, unknown> }).properties;
    expect(properties["fields"]).toMatchObject({ properties: { Email: { type: "string" } } });
    expect(properties["values"]).toEqual({ type: "object" });
  });
});

describe("a model step's answer ports", () => {
  const SCHEMA = {
    type: "object",
    properties: {
      priority: { type: "string", enum: ["high", "normal", "low"] },
      count: { type: "number" },
    },
    required: ["priority", "count"],
  };
  const outSchema = (attr: string) => ({
    type: "object",
    properties: { [attr]: { type: "object" }, text: { type: "string" } },
  });
  const step = (id: string, attr: string) =>
    makeDefinition({ id, ports: [port("out", "output", outSchema(attr))] });

  it.each([
    ["agent.run", "structured", { structured_output_schema: SCHEMA }],
    ["ai.prompt", "structured", { structured_output_schema: SCHEMA }],
    ["ai.extract", "data", { output_schema: SCHEMA }],
  ])("types %s's %s by the answer schema it declares", (id, attr, config) => {
    const definition = step(id, attr);
    const bare = instance({ definition_id: id });
    expect(instancePorts(bare, definition)).toBe(definition.ports);
    const typed = instancePorts(instance({ definition_id: id, config }), definition);
    expect(typed[0]?.schema).toMatchObject({
      properties: { [attr]: SCHEMA, text: { type: "string" } },
    });
  });

  it("types an agent's answer from the agent configured here, and the override wins", () => {
    const definition = step("agent.run", "structured");
    const inline = instance({
      definition_id: "agent.run",
      config: { inline: { output_schema: SCHEMA } },
    });
    expect(instancePorts(inline, definition)[0]?.schema).toMatchObject({
      properties: { structured: SCHEMA },
    });
    const other = {
      type: "object",
      properties: { tier: { type: "string" } },
    };
    const overridden = instance({
      definition_id: "agent.run",
      config: { inline: { output_schema: other }, structured_output_schema: SCHEMA },
    });
    expect(instancePorts(overridden, definition)[0]?.schema).toMatchObject({
      properties: { structured: SCHEMA },
    });
  });

  it("leaves a pinned agent's answer untyped: its format is not in the config", () => {
    const definition = step("agent.run", "structured");
    const pinned = instance({
      definition_id: "agent.run",
      config: { agent: { agent_id: "a", version_id: "v" } },
    });
    expect(instancePorts(pinned, definition)).toBe(definition.ports);
  });

  it("types a classifier's category on every output, including unsure", () => {
    const OUT = {
      type: "object",
      title: "AiClassifyOutput",
      properties: {
        category: { type: "string" },
        reason: { type: "string" },
        confidence: { anyOf: [{ type: "number" }, { type: "null" }] },
      },
    };
    const CLASSIFY = makeDefinition({
      id: "ai.classify",
      ports: [port("in", "input", null), port("other", "output", OUT)],
    });
    const ports = instancePorts(
      instance({
        definition_id: "ai.classify",
        config: { categories: [{ name: "sales" }, { name: "support" }], min_confidence: 0.5 },
      }),
      CLASSIFY,
    );
    expect(ports.map((p) => p.id)).toEqual(["in", "sales", "support", "other", "unsure"]);
    for (const port of ports.slice(1)) {
      expect(port.schema).toMatchObject({
        properties: {
          category: { type: "string", enum: ["sales", "support", "other"] },
          reason: { type: "string" },
        },
      });
    }
  });

  it("leaves a classifier with no categories untyped: its config is not valid yet", () => {
    const OUT = { type: "object", title: "AiClassifyOutput" };
    const CLASSIFY = makeDefinition({
      id: "ai.classify",
      ports: [port("in", "input", null), port("other", "output", OUT)],
    });
    const ports = instancePorts(instance({ definition_id: "ai.classify", config: {} }), CLASSIFY);
    expect(ports.map((p) => p.id)).toEqual(["in", "other"]);
    expect(ports[1]?.schema).toBe(OUT);
  });
});

describe("effectiveDefinition", () => {
  it("is the definition itself when the instance adds no port", () => {
    expect(effectiveDefinition(instance({}), ACTION)).toBe(ACTION);
  });

  it("carries the instance's own ports otherwise", () => {
    const effective = effectiveDefinition(instance({ policy: { on_error: "route" } }), ACTION);
    expect(effective).not.toBe(ACTION);
    expect(effective.ports.some((p) => p.id === ERROR_PORT)).toBe(true);
  });
});

describe("helpers", () => {
  it("reads branch names only from a list of named branches", () => {
    expect(errorBranches({ branches: "no" })).toEqual([]);
    expect(errorBranches({ branches: [{ name: "a" }, { name: "" }, null] })).toEqual(["a"]);
  });

  it("routes errors only under on_error: route", () => {
    expect(routesErrors({ policy: { on_error: "route" } })).toBe(true);
    expect(routesErrors({ policy: { on_error: "fail_run" } })).toBe(false);
    expect(routesErrors({ policy: null })).toBe(false);
  });
});

it("uses localized labels only for synthesized standard ports", () => {
  const definition = makeDefinition({
    id: "ai.classify",
    ports: [port("other", "output", null)],
    standardPortLabels: { error: "Błąd", unsure: "Niepewne" },
  });
  const ports = instancePorts(
    instance({
      definition_id: "ai.classify",
      config: { categories: [{ name: "Error" }], min_confidence: 0.8 },
      policy: { on_error: "route" },
    }),
    definition,
  );
  expect(ports.find((p) => p.id === "error")?.label).toBe("Błąd");
  expect(ports.find((p) => p.id === "unsure")?.label).toBe("Niepewne");
  expect(ports.find((p) => p.id === "Error")?.label).toBe("Error");
});
