"use client";

import { useRef, useState, type ChangeEvent } from "react";
import { ImagePlus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { AgentAvatar } from "@/components/agents/agent-avatar";
import { AvatarColorPicker } from "@/components/ui/avatar-color-picker";
import { Button } from "@/components/ui";
import type { Agent } from "@/types/agents";

interface Props {
  agent: Agent;
  disabled: boolean;
  busy: boolean;
  onUpload: (file: File) => Promise<unknown>;
  onRemove: () => Promise<unknown>;
  onColorChange: (slot: number | null) => Promise<unknown>;
}

/** Existing protected avatar storage and fallback colour controls, exposed beside chat. */
export function ChatAgentIdentityAppearance({
  agent,
  disabled,
  busy,
  onUpload,
  onRemove,
  onColorChange,
}: Props) {
  const t = useTranslations("chat.agentSettings");
  const input = useRef<HTMLInputElement>(null);
  const [version, setVersion] = useState(0);
  const locked = disabled || busy;
  const hasAvatar = agent.has_avatar === true;

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const target = event.currentTarget;
    const file = target.files?.[0];
    target.value = "";
    if (!file) return;
    try {
      await onUpload(file);
      setVersion((current) => current + 1);
    } catch {
      // The mutation reports the failure through the existing toast path.
    }
  }

  async function remove() {
    try {
      await onRemove();
      setVersion((current) => current + 1);
    } catch {
      // The mutation reports the failure through the existing toast path.
    }
  }

  return (
    <section className="space-y-3 border-t pt-4">
      <h3 className="text-sm font-medium">{t("identity")}</h3>
      <p className="text-muted-foreground text-xs">{t("appearanceSaveHint")}</p>
      <div className="flex items-center gap-3">
        <AgentAvatar
          agentId={agent.id}
          slug={agent.slug}
          hasAvatar={hasAvatar}
          colorSlot={agent.avatar_color}
          size="lg"
          version={version}
        />
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={input}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            aria-label={t("avatarFile")}
            className="sr-only"
            disabled={locked}
            onChange={(event) => void upload(event)}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={locked}
            onClick={() => input.current?.click()}
          >
            <ImagePlus className="size-4" aria-hidden />
            {t(hasAvatar ? "avatarReplace" : "avatarUpload")}
          </Button>
          {hasAvatar && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={locked}
              onClick={() => void remove()}
            >
              <Trash2 className="size-4" aria-hidden />
              {t("avatarRemove")}
            </Button>
          )}
          <p className="text-muted-foreground basis-full text-xs">{t("avatarHint")}</p>
        </div>
      </div>
      <div className="space-y-2">
        <p className="text-muted-foreground text-xs font-medium">{t("avatarColor")}</p>
        <AvatarColorPicker
          value={agent.avatar_color ?? null}
          disabled={locked}
          onChange={(slot) => void onColorChange(slot).catch(() => undefined)}
        />
        <p className="text-muted-foreground text-xs">{t("avatarColorHint")}</p>
      </div>
    </section>
  );
}
