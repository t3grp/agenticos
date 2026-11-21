import { beforeEach, describe, expect, it, vi } from "vitest";

import { AUDIT_PAGE_SIZE, listAuditEntries } from "./audit-api";
import { apiClient } from "./api-client";

vi.mock("./api-client", () => ({ apiClient: { get: vi.fn() } }));

const PAGE = { items: [], total: 0 };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiClient.get).mockResolvedValue(PAGE);
});

describe("listAuditEntries", () => {
  it("reads the trail newest first, fifty rows at a time", async () => {
    await expect(listAuditEntries(0)).resolves.toEqual(PAGE);
    expect(apiClient.get).toHaveBeenCalledWith("/audit", {
      params: { skip: "0", limit: String(AUDIT_PAGE_SIZE) },
    });
  });

  it("pages by skip, so the second page is not the first page again", async () => {
    await listAuditEntries(50);

    expect(apiClient.get).toHaveBeenCalledWith("/audit", {
      params: { skip: "50", limit: String(AUDIT_PAGE_SIZE) },
    });
  });
});
