import {
  AppWindow,
  Bell,
  Brain,
  Globe,
  Keyboard,
  KeyRound,
  Shield,
  Slash,
  Smile,
  UserCircle,
} from "lucide-react";

import type { PageTab } from "@/components/dashboard/page-tabs";
import { ROUTES } from "@/lib/constants";

/**
 * The tabs, and the whole of what `/settings/` holds.
 *
 * `/settings/` is *your* configuration: your profile, your account, your
 * notifications, your slash commands, and the MCP connections that carry your
 * credentials. Anything the organization owns - the provider keys it runs on,
 * the MCP servers its agents are built on - is a primary destination in the
 * sidebar and lives at the top level, so no page is claimed by two navigations
 * at once.
 *
 * Hence "Your connections" rather than "Integrations". The word that has to
 * appear in the label is the owner: the sidebar has an MCP page too, and until
 * one of them said "your" and the other said "organization", the difference
 * between them was invisible.
 *
 * This list is therefore an index of the section, not a selection from it, and
 * `settings-tabs.test.ts` fails if the two ever disagree: adding a page here
 * without a tab is how AI providers and MCP servers came to be reachable only
 * by typing their URL.
 *
 * It sits beside `layout.tsx` rather than in it because a route file may export
 * nothing but the route - `next build` rejects the extra export - and this
 * table has to be readable by the test that guards it.
 */
export const SETTINGS_TABS: readonly PageTab[] = [
  { labelKey: "profile", href: ROUTES.SETTINGS_PROFILE, icon: UserCircle },
  {
    labelKey: "account",
    href: ROUTES.SETTINGS_ACCOUNT,
    icon: Shield,
    matchHrefs: [ROUTES.SETTINGS_MOBILE, ROUTES.SETTINGS_ASSISTANT],
  },
  { labelKey: "slashCommands", href: ROUTES.SETTINGS_SLASH_COMMANDS, icon: Slash },
  { labelKey: "memory", href: ROUTES.SETTINGS_MEMORY, icon: Brain },
  { labelKey: "signIns", href: ROUTES.SETTINGS_SIGN_INS, icon: Globe },
  { labelKey: "aiConnections", href: ROUTES.SETTINGS_AI_CONNECTIONS, icon: KeyRound },
  { labelKey: "notifications", href: ROUTES.SETTINGS_NOTIFICATIONS, icon: Bell },
  {
    labelKey: "developerAccess",
    href: ROUTES.SETTINGS_API_KEYS,
    icon: KeyRound,
    matchHrefs: [ROUTES.SETTINGS_MCP],
  },
  // The desktop app's own settings, last and only inside the app: a browser has
  // no blobby, no window and no global shortcut to configure.
  { labelKey: "blobby", href: ROUTES.SETTINGS_BLOBBY, icon: Smile, shellOnly: true },
  { labelKey: "desktopApp", href: ROUTES.SETTINGS_DESKTOP, icon: AppWindow, shellOnly: true },
  { labelKey: "shortcuts", href: ROUTES.SETTINGS_SHORTCUTS, icon: Keyboard, shellOnly: true },
];

/**
 * The same tabs, grouped for the row: three short rows under their own kicker
 * instead of one long strip. `SETTINGS_TABS` stays flat - it is the guard's
 * index - and the groups are built from it, so a tab added to one and not the
 * other fails loudly here rather than silently in the row.
 *
 * The order inside "You" is the spec's, not the flat list's: Notifications
 * moves before Memory and Sign-ins, because what reaches you comes before
 * what the agent remembers about you.
 */
export type SettingsGroup = {
  /** A key in `pages.settings`, not the word itself. */
  headingKey: string;
  tabs: readonly PageTab[];
};

const tabByKey = (labelKey: string): PageTab => {
  const tab = SETTINGS_TABS.find((candidate) => candidate.labelKey === labelKey);
  // i18n-exempt: developer error, never rendered to a reader.
  if (!tab) throw new Error(`Settings tab "${labelKey}" is not in SETTINGS_TABS`);
  return tab;
};

export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  {
    headingKey: "groupYou",
    tabs: ["profile", "account", "notifications", "memory", "signIns"].map(tabByKey),
  },
  {
    headingKey: "groupYourTools",
    tabs: ["slashCommands", "aiConnections", "developerAccess"].map(tabByKey),
  },
  {
    headingKey: "groupThisDevice",
    tabs: ["blobby", "desktopApp", "shortcuts"].map(tabByKey),
  },
];
