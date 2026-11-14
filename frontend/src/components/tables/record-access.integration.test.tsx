import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { RecordAccessDialog } from "./record-access-dialog";
import { TableRowAccess } from "./table-row-access";
import { apiClient } from "@/lib/api-client";
import type { TableRead } from "@/types/tables";
import type { RecordAccess } from "@/lib/table-record-access-api";

vi.mock("@/lib/api-client", () => ({
  apiClient: { get: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/hooks", () => ({
  useMembers: () => ({
    members: [
      {
        id: "m1",
        organization_id: "org",
        user_id: "user-1",
        role: "viewer",
        email: "ada@example.com",
        full_name: "Ada",
        avatar_url: null,
        avatar_color: null,
        joined_at: "2026-01-01T00:00:00Z",
        source: "manual",
      },
    ],
  }),
  useGroups: () => ({
    groups: [
      {
        id: "group-1",
        organization_id: "org",
        name: "Support",
        description: null,
        icon: null,
        monthly_budget_usd: null,
        member_count: 1,
        created_at: "2026-01-01T00:00:00Z",
      },
    ],
  }),
}));

const access: RecordAccess = { table_id: "table", record_id: "row", grants: [] };
const table: TableRead = {
  id: "table",
  name: "Orders",
  description: null,
  visibility: "org",
  owner_user_id: "owner",
  schema_version: 1,
  archived_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: null,
  can_edit: true,
  can_restore: false,
  can_manage_row_access: true,
  row_policy_valid: true,
  columns: [],
  row_policy: null,
};
function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
  Element.prototype.scrollIntoView = () => {};
});
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiClient.get).mockResolvedValue(access);
  vi.mocked(apiClient.put).mockResolvedValue(access);
  vi.mocked(apiClient.delete).mockResolvedValue(undefined);
});

describe("row access", () => {
  it("shares a row with a group as Write, through the row namespace", async () => {
    const user = userEvent.setup();
    render(<RecordAccessDialog tableId="table" recordId="row" />, { wrapper });
    expect(apiClient.get).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Row access" }));
    await screen.findByText("No explicit grants. Table and row rules still apply.");
    await user.click(screen.getByRole("combobox", { name: "Person or group" }));
    await user.click(await screen.findByRole("option", { name: "Support" }));
    await user.click(screen.getByRole("combobox", { name: "Permission" }));
    await user.click(await screen.findByRole("option", { name: "Write" }));
    await user.click(screen.getByRole("button", { name: "Save grant" }));
    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith("/tables/table/records/row/access/grants", {
        subject_group_id: "group-1",
        level: "write",
        expires_at: null,
      }),
    );
    expect(screen.getByText(/does not allow deletion/)).toBeVisible();
  });

  it("downgrades Read/Write and revokes group access using the group endpoint", async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      ...access,
      grants: [
        {
          id: "grant",
          subject_user_id: null,
          subject_email: null,
          subject_group_id: "group-1",
          subject_group_name: "Support",
          level: "write",
          expires_at: null,
        },
      ],
    });
    const user = userEvent.setup();
    render(<RecordAccessDialog tableId="table" recordId="row" />, { wrapper });
    await user.click(screen.getByRole("button", { name: "Row access" }));
    const picker = await screen.findByRole("combobox", { name: "Permission for Support" });
    await user.click(picker);
    await user.click(await screen.findByRole("option", { name: "Read" }));
    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith("/tables/table/records/row/access/grants", {
        subject_group_id: "group-1",
        level: "read",
        expires_at: null,
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Remove access for Support" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Remove access for Support" }));
    await waitFor(() =>
      expect(apiClient.delete).toHaveBeenCalledWith(
        "/tables/table/records/row/access/group-grants/group-1",
      ),
    );
  });

  it("repairs an invalid policy with an explicit restricted rule", async () => {
    render(<TableRowAccess table={{ ...table, row_policy_valid: false }} />, { wrapper });
    expect(screen.getByRole("alert")).toHaveTextContent("Records are blocked");
    fireEvent.click(screen.getByRole("button", { name: "Save row rules" }));
    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith("/tables/table/row-policy", { rule: "grants" }),
    );
  });

  it("does not expose policy editing to a row writer", () => {
    render(<TableRowAccess table={{ ...table, can_edit: false, can_manage_row_access: false }} />, {
      wrapper,
    });
    expect(screen.queryByRole("button", { name: "Save row rules" })).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
