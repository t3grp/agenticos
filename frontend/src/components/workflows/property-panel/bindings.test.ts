import { describe, expect, it, vi } from "vitest";

import type { Binding } from "@/lib/workflows/types";
import {
  DEBUG_ECHO,
  DEBUG_ECHO_OUTPUT,
  INTEGER,
  STRING,
  echo,
  edge,
  graph,
  makeCatalog,
  makeDefinition,
  node,
  objectSchema,
  port,
} from "@/components/workflows/validation/fixtures";

import {
  applyRebase,
  bindingFor,
  candidateByKey,
  candidateKey,
  isNodeOutput,
  literalBinding,
  literalValueOf,
  nodeOutputBinding,
  rebaseClear,
  rebaseRemove,
  rebaseSwap,
  sourceCandidates,
} from "./bindings";

const catalog = makeCatalog([DEBUG_ECHO]);

/** A → B, so A dominates B and is a binding candidate for it. */
const chain = graph({
  entry: "A",
  nodes: [echo("A"), echo("B")],
  edges: [edge("e", "A", "out", "B", "in")],
});

describe("binding accessors", () => {
  const literal = literalBinding("N", "field", "hi");
  const ref = nodeOutputBinding("N", "field2", "A", "out");
  const bindings = [literal, ref];

  it("finds a binding by node and field, or nothing", () => {
    expect(bindingFor(bindings, "N", "field")).toBe(literal);
    expect(bindingFor(bindings, "N", "missing")).toBeUndefined();
    expect(bindingFor(bindings, "other", "field")).toBeUndefined();
  });

  it("reads a literal value only from a literal source", () => {
    expect(literalValueOf(literal)).toBe("hi");
    expect(literalValueOf(ref)).toBeUndefined();
    expect(literalValueOf(undefined)).toBeUndefined();
  });

  it("recognises a node-output source", () => {
    expect(isNodeOutput(ref)).toBe(true);
    expect(isNodeOutput(literal)).toBe(false);
    expect(isNodeOutput(undefined)).toBe(false);
  });

  it("builds literal and node-output bindings", () => {
    expect(literalBinding("N", "f", 3)).toEqual({
      target_node_id: "N",
      target_field: "f",
      source: { kind: "literal", value: 3 },
    });
    expect(nodeOutputBinding("N", "f", "A", "out")).toEqual({
      target_node_id: "N",
      target_field: "f",
      source: { kind: "node_output", node_id: "A", port: "out", field_path: [] },
    });
    expect(nodeOutputBinding("N", "f", "A", "out", ["echoed"]).source).toEqual({
      kind: "node_output",
      node_id: "A",
      port: "out",
      field_path: ["echoed"],
    });
  });
});

describe("candidateKey / candidateByKey", () => {
  it("encodes and resolves a candidate", () => {
    expect(candidateKey("A", "out")).toBe("A::out");
    const candidates = sourceCandidates(chain, catalog, "B", DEBUG_ECHO_OUTPUT);
    expect(candidateByKey(candidates, "A::out")).toBeDefined();
    expect(candidateByKey(candidates, "nope")).toBeUndefined();
  });

  it("appends an encoded field path, so a field of a port has its own key", () => {
    expect(candidateKey("A", "out", ["echoed"])).toBe("A::out::echoed");
    expect(candidateKey("A", "out", ["usage", "input tokens"])).toBe(
      "A::out::usage/input%20tokens",
    );
    expect(candidateKey("A", "out", ["a/b"])).not.toBe(candidateKey("A", "out", ["a", "b"]));
  });
});

describe("sourceCandidates", () => {
  const label = {
    nodeLabel: "Echo · A",
    portLabel: "out",
    nodeId: "A",
    port: "out",
    dynamic: false,
  };

  it("offers a reachable, type-compatible upstream output port", () => {
    const candidates = sourceCandidates(chain, catalog, "B", DEBUG_ECHO_OUTPUT);
    expect(candidates).toEqual([
      { ...label, key: "A::out", fieldPath: [], typeToken: "DebugEchoOutput" },
    ]);
  });

  it("offers each field of an output whose type matches, since a scalar cannot take the whole port", () => {
    expect(sourceCandidates(chain, catalog, "B", STRING)).toEqual([
      { ...label, key: "A::out::echoed", fieldPath: ["echoed"], typeToken: "string" },
    ]);
    expect(
      sourceCandidates(chain, catalog, "B", { type: "string", format: "date-time" }).map(
        (candidate) => candidate.fieldPath,
      ),
    ).toEqual([["received_at"]]);
  });

  it("offers a free-form value to any field, for a field inside it", () => {
    const MAP = makeDefinition({
      id: "data.map",
      ports: [
        port(
          "out",
          "output",
          objectSchema("MapOutput", { values: { type: "object", additionalProperties: true } }, []),
        ),
      ],
    });
    const mapped = graph({
      entry: "M",
      nodes: [node("M", "data.map"), echo("B")],
      edges: [edge("e", "M", "out", "B", "in")],
    });
    const offered = sourceCandidates(mapped, makeCatalog([DEBUG_ECHO, MAP]), "B", STRING);
    expect(offered.map((candidate) => [candidate.fieldPath, candidate.dynamic])).toEqual([
      [["values"], true],
    ]);
  });

  it("drops a port and fields whose types do not match the target", () => {
    expect(sourceCandidates(chain, catalog, "B", INTEGER)).toEqual([]);
  });

  describe("a trigger with a declared payload", () => {
    const webhook = makeDefinition({
      id: "trigger.webhook",
      name: "Webhook",
      ports: [
        port(
          "out",
          "output",
          objectSchema("WebhookTriggerOutput", { body: { type: "object" }, delivery_id: STRING }, [
            "delivery_id",
          ]),
        ),
      ],
    });
    const hooked = (config: Record<string, unknown>) =>
      graph({
        entry: "W",
        nodes: [node("W", "trigger.webhook", config), echo("B")],
        edges: [edge("e", "W", "out", "B", "in")],
      });
    const both = makeCatalog([DEBUG_ECHO, webhook]);

    it("offers a declared field with its type, and the field is not dynamic", () => {
      const offered = sourceCandidates(
        hooked({ payload_fields: [{ name: "title", type: "text" }] }),
        both,
        "B",
        STRING,
      );
      expect(offered.map((candidate) => [candidate.fieldPath, candidate.dynamic])).toEqual([
        [["body", "title"], false],
        [["delivery_id"], false],
      ]);
      expect(offered[0]?.typeToken).toBe("string");
    });

    it("offers an undeclared payload as one dynamic value, for a field inside it", () => {
      const offered = sourceCandidates(hooked({}), both, "B", STRING);
      expect(offered.map((candidate) => [candidate.fieldPath, candidate.dynamic])).toEqual([
        [["body"], true],
        [["delivery_id"], false],
      ]);
    });

    it("offers a nested Tier 2 field from the schema, three levels in", () => {
      const github = makeDefinition({
        id: "trigger.github",
        name: "GitHub",
        ports: [
          port(
            "out",
            "output",
            objectSchema("GithubTriggerOutput", { payload: { type: "object" } }, []),
          ),
        ],
      });
      const delivered = graph({
        entry: "G",
        nodes: [
          node("G", "trigger.github", {
            payload_schema: {
              type: "object",
              properties: {
                pull_request: { type: "object", properties: { title: { type: "string" } } },
              },
            },
          }),
          echo("B"),
        ],
        edges: [edge("e", "G", "out", "B", "in")],
      });
      const offered = sourceCandidates(delivered, makeCatalog([DEBUG_ECHO, github]), "B", STRING);
      expect(offered.map((candidate) => [candidate.fieldPath, candidate.dynamic])).toEqual([
        [["payload", "pull_request", "title"], false],
      ]);
    });
  });

  describe("a model step with a declared answer", () => {
    const agent = makeDefinition({
      id: "agent.run",
      name: "Run an agent",
      ports: [
        port(
          "out",
          "output",
          objectSchema("AgentRunOutput", { structured: { type: "object" }, text: STRING }, [
            "text",
          ]),
        ),
      ],
    });
    const run = (config: Record<string, unknown>) =>
      graph({
        entry: "A",
        nodes: [node("A", "agent.run", config), echo("B")],
        edges: [edge("e", "A", "out", "B", "in")],
      });
    const answered = makeCatalog([DEBUG_ECHO, agent]);
    const SCHEMA = {
      type: "object",
      properties: {
        priority: { type: "string", enum: ["high", "normal", "low"] },
        count: { type: "number" },
      },
    };

    it("offers a declared answer field with its type, and the field is not dynamic", () => {
      const offered = sourceCandidates(
        run({ structured_output_schema: SCHEMA }),
        answered,
        "B",
        STRING,
      );
      expect(offered.map((candidate) => [candidate.fieldPath, candidate.dynamic])).toEqual([
        [["structured", "priority"], false],
        [["text"], false],
      ]);
      expect(offered[0]?.typeToken).toBe("string");
    });

    it("offers an undeclared answer as one dynamic value, for a field inside it", () => {
      const offered = sourceCandidates(run({}), answered, "B", STRING);
      expect(offered.map((candidate) => [candidate.fieldPath, candidate.dynamic])).toEqual([
        [["structured"], true],
        [["text"], false],
      ]);
    });

    it("offers a classifier's category as a declared string field", () => {
      const classify = makeDefinition({
        id: "ai.classify",
        name: "Classify",
        ports: [
          port(
            "sales",
            "output",
            objectSchema("AiClassifyOutput", { category: STRING, reason: STRING }, [
              "category",
              "reason",
            ]),
          ),
        ],
      });
      const classified = graph({
        entry: "C",
        nodes: [
          node("C", "ai.classify", {
            categories: [{ name: "sales" }, { name: "support" }],
          }),
          echo("B"),
        ],
        edges: [edge("e", "C", "sales", "B", "in")],
      });
      const offered = sourceCandidates(
        classified,
        makeCatalog([DEBUG_ECHO, classify]),
        "B",
        STRING,
      );
      expect(offered.map((candidate) => [candidate.fieldPath, candidate.dynamic])).toEqual([
        [["category"], false],
        [["reason"], false],
      ]);
      expect(offered[0]?.typeToken).toBe("string");
    });
  });

  it("skips a reachable node whose definition is unknown", () => {
    const broken = graph({
      entry: "X",
      nodes: [node("X", "missing"), echo("B")],
      edges: [edge("e", "X", "out", "B", "in")],
    });
    expect(sourceCandidates(broken, catalog, "B", DEBUG_ECHO_OUTPUT)).toEqual([]);
  });

  describe("a nested output", () => {
    const deep = (levels: number) => {
      let schema = objectSchema("Leaf", { value: STRING }, ["value"]);
      for (let level = 1; level < levels; level += 1) {
        schema = objectSchema(`Level${level}`, { next: schema }, ["next"]);
      }
      return schema;
    };
    const producer = makeDefinition({
      id: "test.producer",
      output_schema: deep(1),
      ports: [port("in", "input", null), port("out", "output", deep(5))],
    });
    const consumer = makeDefinition({ id: "test.consumer", ports: [port("in", "input", null)] });
    const g = graph({
      entry: "P",
      nodes: [node("P", "test.producer"), node("C", "test.consumer")],
      edges: [edge("e", "P", "out", "C", "in")],
    });
    const both = makeCatalog([producer, consumer]);

    it("offers a nested object at the third level and nothing at the fourth", () => {
      // `deep(5)` nests Level4 > Level3 > Level2 > Level1 > Leaf, each under `next`.
      const at = (title: string) =>
        sourceCandidates(g, both, "C", objectSchema(title, {}, [])).map((c) => c.fieldPath);
      expect(at("Level1")).toEqual([["next", "next", "next"]]);
      expect(at("Leaf")).toEqual([]);
    });

    it("reaches a scalar at the depth limit but not beyond it", () => {
      // Five levels deep, the scalar sits at next/next/next/next/value: past the limit.
      expect(sourceCandidates(g, both, "C", STRING)).toEqual([]);

      const shallow = makeDefinition({
        id: "test.shallow",
        ports: [port("out", "output", deep(3))],
      });
      const reads = graph({
        entry: "S",
        nodes: [node("S", "test.shallow"), node("C", "test.consumer")],
        edges: [edge("e", "S", "out", "C", "in")],
      });
      expect(
        sourceCandidates(reads, makeCatalog([shallow, consumer]), "C", STRING).map(
          (c) => c.fieldPath,
        ),
      ).toEqual([["next", "next", "value"]]);
    });
  });
});

describe("rebase", () => {
  const bindings: Binding[] = [
    literalBinding("N", "mappings", "head-only"),
    literalBinding("N", "mappings/0/value", "zero"),
    nodeOutputBinding("N", "mappings/1/value", "A", "out"),
    literalBinding("N", "mappings/2/value", "two"),
    literalBinding("N", "other", "unrelated"),
    literalBinding("Other", "mappings/1/value", "different node"),
  ];

  it("removes a row and shifts later rows down", () => {
    const rebase = rebaseRemove(bindings, "N", ["mappings"], 1);
    expect(rebase.toRemove).toEqual(["mappings/1/value", "mappings/2/value"]);
    expect(rebase.toUpsert).toEqual([
      {
        target_node_id: "N",
        target_field: "mappings/1/value",
        source: { kind: "literal", value: "two" },
      },
    ]);
  });

  it("swaps two rows", () => {
    const rebase = rebaseSwap(bindings, "N", ["mappings"], 0, 2);
    expect(rebase.toRemove).toEqual(["mappings/0/value", "mappings/2/value"]);
    expect(rebase.toUpsert.map((b) => b.target_field)).toEqual([
      "mappings/2/value",
      "mappings/0/value",
    ]);
  });

  it("clears every binding strictly nested under the prefix", () => {
    const rebase = rebaseClear(bindings, "N", ["mappings"]);
    expect(rebase.toRemove).toEqual(["mappings/0/value", "mappings/1/value", "mappings/2/value"]);
  });

  it("ignores a non-numeric index and a differently-headed path", () => {
    const rows: Binding[] = [
      literalBinding("N", "mappings/x/value", "not-a-row"),
      literalBinding("N", "elsewhere/0/value", "other-head"),
      literalBinding("N", "mappings/0/value", "zero"),
    ];
    const rebase = rebaseRemove(rows, "N", ["mappings"], 0);
    expect(rebase.toRemove).toEqual(["mappings/0/value"]);
    expect(rebase.toUpsert).toEqual([]);
  });

  it("applies a rebase as removals then writes, in order", () => {
    const upsert = vi.fn();
    const remove = vi.fn();
    const calls: string[] = [];
    upsert.mockImplementation(() => calls.push("upsert"));
    remove.mockImplementation(() => calls.push("remove"));
    applyRebase(rebaseRemove(bindings, "N", ["mappings"], 1), "N", upsert, remove);
    expect(remove).toHaveBeenCalledWith("N", "mappings/1/value");
    expect(remove).toHaveBeenCalledWith("N", "mappings/2/value");
    expect(upsert).toHaveBeenCalledTimes(1);
    expect(calls).toEqual(["remove", "remove", "upsert"]);
  });
});
