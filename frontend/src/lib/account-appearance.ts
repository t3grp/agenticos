import { useAuthStore } from "@/stores/auth-store";
import { useThemeStore, type Theme } from "@/stores/theme-store";
import { useMotionStore } from "@/stores/motion-store";
import { useVoiceGlowStore } from "@/stores/voice-glow-store";
import { useWorkflowNodeStyleStore } from "@/stores/workflow-node-style-store";
import { isLook } from "./appearance";
import {
  applyAppearanceVariables,
  DEFAULT_APPEARANCE,
  type UserAppearance,
} from "./user-appearance";

// A response from before sign-out must not dress a later session, even if the
// same account signs back in before the old request finishes.
let generation = 0;
export const appearanceGeneration = () => generation;

export function applyAccountAppearance(preferences: UserAppearance): void {
  useThemeStore.setState({ theme: preferences.theme, look: preferences.look });
  useMotionStore.setState({ motion: preferences.motion });
  useVoiceGlowStore.setState({ style: preferences.voice_glow });
  useWorkflowNodeStyleStore.setState({ style: preferences.workflow_nodes });
  if (typeof document !== "undefined") applyAppearanceVariables(preferences);
}

export function isCurrentAppearanceOwner(owner: string, epoch: number): boolean {
  return generation === epoch && useAuthStore.getState().user?.id === owner;
}

export function adoptAccountAppearance(
  owner: string,
  epoch: number,
  preferences: UserAppearance,
): boolean {
  if (!isCurrentAppearanceOwner(owner, epoch)) return false;
  applyAccountAppearance(preferences);
  return true;
}

export function resetAccountAppearance(): void {
  generation += 1;
  applyAccountAppearance(DEFAULT_APPEARANCE);
}

/** Only the platform's own successful preference result may dress this console. */
export function appearanceChosenBy(toolName: string, result: string): void {
  if (toolName !== "agenticos_set_my_preferences") return;
  let value: unknown;
  try {
    value = JSON.parse(result);
  } catch {
    return;
  }
  if (!value || typeof value !== "object" || !("theme" in value) || !("look" in value)) return;
  const theme: Theme | null =
    value.theme === "light" || value.theme === "dark" || value.theme === "system"
      ? value.theme
      : null;
  const look = isLook(value.look) ? value.look : null;
  useThemeStore.setState({
    ...(theme === null ? {} : { theme }),
    ...(look === null ? {} : { look }),
  });
}
