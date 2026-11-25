import { describe, expect, it } from "vitest";

import type { SourceCandidate } from "./bindings";
import {
  matchSuggestions,
  SHOWN_SUGGESTIONS,
  templateSuggestions,
  type Suggestion,
} from "./template-suggest";

function candidate(over: Partial<SourceCandidate>): SourceCandidate {
  return {
    key: "k",
    nodeId: "t",
    port: "out",
    fieldPath: [],
    nodeLabel: "Manual",
    portLabel: "Out",
    typeToken: "object",
    dynamic: false,
    ...over,
  };
}

const NAMES = new Map([["t", "Manual"]]);

describe("templateSuggestions", () => {
  it("offers what the Data menu offers, and inside a free-form value what a run saw", () => {
    const suggestions = templateSuggestions(
      [
        candidate({ fieldPath: ["triggered_by"], typeToken: "string" }),
        candidate({ fieldPath: ["payload"], typeToken: "object", dynamic: true }),
      ],
      {
        t: {
          output: {
            payload: {
              customer: "Contoso",
              seats: 12,
              tags: ["a"],
              note: null,
              deep: { a: { b: { c: 1 } } },
            },
          },
          error: null,
          runId: "r",
        },
      },
      NAMES,
    );
    expect(suggestions.map((suggestion) => [suggestion.text, suggestion.type])).toEqual([
      ["{{Manual.triggered_by}}", "string"],
      ["{{Manual.payload}}", "object"],
      ["{{Manual.payload.customer}}", "string"],
      ["{{Manual.payload.seats}}", "number"],
      ["{{Manual.payload.tags}}", "array"],
      ["{{Manual.payload.note}}", "null"],
      ["{{Manual.payload.deep}}", "object"],
      ["{{Manual.payload.deep.a}}", "object"],
      // Three levels in, and no further.
      ["{{Manual.payload.deep.a.b}}", "object"],
    ]);
    expect(suggestions[2]).toMatchObject({ step: "Manual", path: ".payload.customer" });
  });

  it("offers a free-form value alone with no run, or one that held nothing, once each", () => {
    const payload = candidate({ fieldPath: ["payload"], dynamic: true });
    expect(templateSuggestions([payload, payload], {}, NAMES).map((s) => s.text)).toEqual([
      "{{Manual.payload}}",
    ]);
    const seenNull = { t: { output: { payload: null }, error: null, runId: "r" } };
    expect(templateSuggestions([payload], seenNull, NAMES)).toHaveLength(1);
    const deeper = candidate({ fieldPath: ["payload", "x"], dynamic: true });
    expect(templateSuggestions([deeper], seenNull, new Map())[0]?.step).toBe("t");
  });

  it("offers declared fields with their types, with no test run at all", () => {
    // A webhook whose body declares `title` and `seats`: the picker offers
    // both from the schema, and no run's data is needed - or consulted.
    const suggestions = templateSuggestions(
      [
        candidate({ fieldPath: ["body"], typeToken: "Payload" }),
        candidate({ fieldPath: ["body", "title"], typeToken: "string" }),
        candidate({ fieldPath: ["body", "seats"], typeToken: "integer" }),
      ],
      {},
      NAMES,
    );
    expect(suggestions.map((suggestion) => [suggestion.text, suggestion.type])).toEqual([
      ["{{Manual.body}}", "Payload"],
      ["{{Manual.body.title}}", "string"],
      ["{{Manual.body.seats}}", "integer"],
    ]);
  });

  it("keeps observed fields as the fallback for a dynamic value only", () => {
    const suggestions = templateSuggestions(
      [candidate({ fieldPath: ["body"], typeToken: "object", dynamic: true })],
      {
        t: {
          output: { body: { title: "Hi", seats: 3 } },
          error: null,
          runId: "r",
        },
      },
      NAMES,
    );
    expect(suggestions.map((suggestion) => [suggestion.text, suggestion.type])).toEqual([
      ["{{Manual.body}}", "object"],
      ["{{Manual.body.title}}", "string"],
      ["{{Manual.body.seats}}", "number"],
    ]);
  });

  it("lets a declared field beat an observed one of the same name", () => {
    // The run saw `seats` as a number; the declaration says it is text.
    // The dynamic candidate comes first, so only the declared-first order
    // keeps the declared type.
    const suggestions = templateSuggestions(
      [
        candidate({ fieldPath: ["body"], typeToken: "object", dynamic: true }),
        candidate({ fieldPath: ["body", "seats"], typeToken: "string" }),
      ],
      { t: { output: { body: { seats: 3 } }, error: null, runId: "r" } },
      NAMES,
    );
    const seats = suggestions.filter((suggestion) => suggestion.text === "{{Manual.body.seats}}");
    expect(seats).toHaveLength(1);
    expect(seats[0]?.type).toBe("string");
  });
});

describe("matchSuggestions", () => {
  const offered = (text: string): Suggestion => ({ text, step: text, path: "", type: "string" });

  it("keeps the names that hold what was typed, ones starting with it first", () => {
    const matched = matchSuggestions(
      [offered("Read customer"), offered("Customer"), offered("Other")],
      "CUST",
    );
    expect(matched.map((suggestion) => suggestion.step)).toEqual(["Customer", "Read customer"]);
  });

  it("shows at most a screenful", () => {
    const many = Array.from({ length: SHOWN_SUGGESTIONS + 5 }, (_, index) => offered(`s${index}`));
    expect(matchSuggestions(many, "")).toHaveLength(SHOWN_SUGGESTIONS);
  });
});
