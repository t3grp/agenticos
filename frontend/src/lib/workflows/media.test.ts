import { describe, expect, it } from "vitest";

import type { WorkflowGraph } from "./types";
import { hasBillableMedia } from "./media";

function graph(ids: string[]): WorkflowGraph {
  return {
    entry_node_id: "node-0",
    nodes: ids.map((definition_id, index) => ({
      id: `node-${index}`,
      definition_id,
      definition_version: 1,
      config: {},
      layout: { x: 0, y: 0 },
    })),
    edges: [],
    bindings: [],
    scopes: [],
  };
}

describe("hasBillableMedia", () => {
  it("finds image and video generation nodes", () => {
    expect(hasBillableMedia(graph(["core.input", "image.generate"]))).toBe(true);
    expect(hasBillableMedia(graph(["video.generate"]))).toBe(true);
  });

  it("does not warn for an Asset-only workflow", () => {
    expect(hasBillableMedia(graph(["asset.input", "image.transform"]))).toBe(false);
    expect(hasBillableMedia(null)).toBe(false);
  });
});
