import type { WorkflowGraph } from "./types";

/** Whether a run of this graph can submit a new image or video generation. */
export function hasBillableMedia(graph: WorkflowGraph | null | undefined): boolean {
  return (
    graph?.nodes.some(
      (node) => node.definition_id === "image.generate" || node.definition_id === "video.generate",
    ) ?? false
  );
}
