import { describe, expect, it } from "vitest";

import { NAV_GROUPS, navItemVisible, navSectionFor } from "./nav-sections";
import { ROUTES } from "./constants";
import { Perm } from "@/types/permissions";
import type { Permission } from "@/types/permissions";

describe("navSectionFor", () => {
  it("names the entry and its own hue, and says when the path is the section's own page", () => {
    expect(navSectionFor("/agents")).toMatchObject({ domain: "build", isRoot: true });
    expect(navSectionFor("/agents")?.item.href).toBe("/agents");
    expect(navSectionFor("/rag")).toMatchObject({ domain: "knowledge", isRoot: true });
    expect(navSectionFor("/connectors")).toMatchObject({ domain: "workspace", isRoot: true });
    expect(navSectionFor("/dashboard")).toMatchObject({ domain: "use", isRoot: true });
    expect(navSectionFor("/agent-messages")).toMatchObject({ domain: "use", isRoot: true });
    expect(navSectionFor("/agent-messages")?.item.href).toBe("/agent-messages");
    // The hue travels with the entry, not the group, now that More holds
    // entries from every section.
    expect(navSectionFor("/workflows")).toMatchObject({ domain: "build", isRoot: true });
    expect(navSectionFor("/tables")).toMatchObject({ domain: "knowledge", isRoot: true });
  });

  it("places a sub-page in its section without calling it the section's page", () => {
    expect(navSectionFor("/agents/abc")).toMatchObject({ domain: "build", isRoot: false });
    // The Advanced connections are sub-pages of Connectors, not entries of their own.
    expect(navSectionFor("/connectors/vault")).toMatchObject({
      domain: "workspace",
      isRoot: false,
    });
  });

  it("calls each tab of a tabbed section its page, and nothing deeper", () => {
    // The admin page is its tabs, each a path of its own; it lost its icon
    // because none of them was exactly `/admin`.
    expect(navSectionFor("/admin/users")).toMatchObject({ domain: "admin", isRoot: true });
    expect(navSectionFor("/en/admin/system")).toMatchObject({ isRoot: true });
    expect(navSectionFor("/admin/users/u-1")).toMatchObject({ isRoot: false });
  });

  it("strips a locale prefix before it decides", () => {
    expect(navSectionFor("/en/skills")).toMatchObject({ domain: "knowledge", isRoot: true });
  });

  it("answers null outside every section", () => {
    expect(navSectionFor("/settings/profile")).toBeNull();
  });
});

describe("the sidebar's shape", () => {
  it("is five daily entries, Apps, Projects, then one flat More list", () => {
    expect(NAV_GROUPS.map((group) => group.labelKey)).toEqual([
      null,
      "apps",
      "projects",
      "more",
      "organization",
    ]);
    expect(NAV_GROUPS[0]!.items.map((item) => item.labelKey)).toEqual([
      "dashboard",
      "chat",
      "inbox",
    ]);
  });

  it("lists More in one flat order, with no sub-headings to add", () => {
    const more = NAV_GROUPS.find((group) => group.labelKey === "more");
    expect(more?.items.map((item) => item.labelKey)).toEqual([
      "agentMessages",
      "agents",
      "workflows",
      "routines",
      "datasets",
      "knowledgeBases",
      "tables",
      "skills",
      "context",
      "documents",
      "activity",
      "failureLearning",
      "backgroundWork",
      "connectors",
      "adminOverview",
    ]);
  });

  it("keeps each More entry's own hue, so the title still knows its section", () => {
    const more = NAV_GROUPS.find((group) => group.labelKey === "more")!;
    const domains = Object.fromEntries(more.items.map((item) => [item.labelKey, item.domain]));
    expect(domains).toEqual({
      agentMessages: "use",
      agents: "build",
      workflows: "build",
      routines: "build",
      datasets: "build",
      knowledgeBases: "knowledge",
      tables: "knowledge",
      skills: "knowledge",
      context: "knowledge",
      documents: "knowledge",
      activity: "build",
      failureLearning: "build",
      backgroundWork: "build",
      connectors: "workspace",
      adminOverview: "admin",
    });
  });

  it("keeps Admin last in More, separated and admin-only", () => {
    const more = NAV_GROUPS.find((group) => group.labelKey === "more")!;
    const admin = more.items.at(-1)!;
    expect(admin.labelKey).toBe("adminOverview");
    expect(admin.adminOnly).toBe(true);
    expect(more.items.slice(0, -1).some((item) => item.adminOnly)).toBe(false);
  });
});

describe("the Connectors nav entry", () => {
  const connectors = () => {
    const group = NAV_GROUPS.find((g) => g.labelKey === "more");
    return group?.items.find((item) => item.labelKey === "connectors");
  };
  const holding =
    (...granted: Permission[]) =>
    (permission: Permission) =>
      granted.includes(permission);

  it("is the one entry in More opening to any of the four areas", () => {
    const group = NAV_GROUPS.find((g) => g.labelKey === "more");
    expect(group?.items).toContainEqual(
      expect.objectContaining({
        labelKey: "connectors",
        href: ROUTES.CONNECTORS,
        permissions: [Perm.agentsView, Perm.secretsView, Perm.connectionsView, Perm.networksView],
      }),
    );
  });

  it("shows to a role that can see only the vault", () => {
    expect(navItemVisible(connectors()!, holding(Perm.secretsView))).toBe(true);
  });

  it("shows to a role that can see only the connections", () => {
    expect(navItemVisible(connectors()!, holding(Perm.connectionsView))).toBe(true);
  });

  it("shows to a role that can see only agents", () => {
    expect(navItemVisible(connectors()!, holding(Perm.agentsView))).toBe(true);
  });

  it("shows to a role that can see only networks", () => {
    expect(navItemVisible(connectors()!, holding(Perm.networksView))).toBe(true);
  });

  it("stays hidden from a role with none of the four", () => {
    expect(navItemVisible(connectors()!, holding(Perm.workflowsView))).toBe(false);
  });
});

describe("role navigation preferences", () => {
  const item = (labelKey: string) =>
    NAV_GROUPS.flatMap((group) => group.items).find((entry) => entry.labelKey === labelKey)!;

  it("can hide both permission-gated and ungated entries without granting permissions", () => {
    expect(navItemVisible(item("agents"), () => true, ["agents"])).toBe(false);
    expect(navItemVisible(item("chat"), () => true, ["chat"])).toBe(false);
    expect(navItemVisible(item("agents"), () => false, [])).toBe(false);
    expect(navItemVisible(item("agents"), () => true, [])).toBe(true);
  });

  it("keeps recovery entries even if stale or invalid preferences name them", () => {
    for (const name of ["dashboard", "organizations", "adminOverview"]) {
      expect(navItemVisible(item(name), () => true, [name], false)).toBe(true);
    }
  });

  it("hides every configurable entry while permissions or policy are unknown", () => {
    for (const entry of NAV_GROUPS.flatMap((group) => group.items)) {
      const recovery = ["dashboard", "organizations", "adminOverview"].includes(entry.labelKey);
      expect(navItemVisible(entry, () => true, [], false)).toBe(recovery);
    }
  });
});
