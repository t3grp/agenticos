import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { clearDriveResume, readDriveResume, saveDriveResume } from "./drive-oauth-resume";
import type { SyncSourceCreate } from "./rag-api";

const KEY = "syncSourceDriveResume";
const MINUTE = 60 * 1000;

const FORM: SyncSourceCreate = {
  name: "Engineering docs",
  connector_type: "gdrive",
  collection_name: "handbook",
  config: { folder_id: "1AbC" },
  secret_id: null,
  connected_account_id: null,
  organizational_unit: null,
  sync_mode: "full",
  schedule_minutes: null,
};

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  window.sessionStorage.clear();
});

describe("the snapshot a Google consent leaves behind", () => {
  it("reads back the form and the accounts that were held, until it is cleared", () => {
    saveDriveResume(FORM, ["account-1"], 1_000);

    const read = readDriveResume(2_000);

    expect(read).toEqual({ form: FORM, knownAccountIds: ["account-1"], savedAt: 1_000 });
    // Still there for a second read: only the wizard that took it forgets it.
    expect(readDriveResume(2_000)).toEqual(read);
    clearDriveResume();
    expect(readDriveResume(2_000)).toBeNull();
  });

  it("is not restored after ten minutes, the lifetime of the return cookie", () => {
    saveDriveResume(FORM, [], 0);

    expect(readDriveResume(10 * MINUTE)).not.toBeNull();
    expect(readDriveResume(10 * MINUTE + 1)).toBeNull();
  });

  it("is ignored when it is not JSON", () => {
    window.sessionStorage.setItem(KEY, "{not json");

    expect(readDriveResume()).toBeNull();
  });

  it("is ignored when its shape is not a draft", () => {
    window.sessionStorage.setItem(
      KEY,
      JSON.stringify({ form: { name: 3 }, knownAccountIds: [], savedAt: 0 }),
    );

    expect(readDriveResume(0)).toBeNull();
  });
});
