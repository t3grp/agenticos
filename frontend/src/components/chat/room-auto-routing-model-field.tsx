"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { ChatModelMenu } from "@/components/chat/chat-model-menu";
import { usableForAgents } from "@/lib/model-profiles";
import type { ModelProfile, ProviderInfo } from "@/types/providers";

interface Props {
  profiles: ModelProfile[];
  catalog: ProviderInfo[];
  status: "loaded" | "pending" | "failed";
  value: string | null;
  disabled: boolean;
  onRetry: () => void;
  onChange: (profileId: string) => void;
}

/** Reuse the saved chat model picker, limited to explicit organization profiles. */
export function RoomAutoRoutingModelField({
  profiles,
  catalog,
  status,
  value,
  disabled,
  onRetry,
  onChange,
}: Props) {
  const t = useTranslations("chat.roomAutoRouting");
  const tc = useTranslations("common");
  const organizationProfiles = profiles.filter((profile) => profile.usage_scope === "organization");
  const selected = organizationProfiles.find((profile) => profile.id === value);
  const usable = selected !== undefined && usableForAgents(selected);

  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{t("modelProfile")}</p>
      <p className="text-muted-foreground text-xs">{t("organizationProfileHint")}</p>
      {status === "pending" && (
        <p role="status" className="text-muted-foreground text-xs">
          {tc("loading")}
        </p>
      )}
      {status === "failed" && (
        <div role="alert" className="flex items-center gap-2 text-xs">
          <span className="text-destructive">{t("profilesUnavailable")}</span>
          <Button type="button" variant="ghost" size="sm" onClick={onRetry}>
            {tc("retry")}
          </Button>
        </div>
      )}
      {status === "loaded" && organizationProfiles.length === 0 && (
        <p className="text-muted-foreground text-xs">{t("noOrganizationProfiles")}</p>
      )}
      <fieldset disabled={disabled || status !== "loaded"} className="min-w-0">
        <ChatModelMenu
          profiles={organizationProfiles}
          catalog={catalog}
          value={value}
          currentProvider={selected?.provider}
          onChange={onChange}
        />
      </fieldset>
      {status === "loaded" && value !== null && !usable && (
        <p role="alert" className="text-destructive text-xs">
          {t("modelUnavailable")}
        </p>
      )}
    </div>
  );
}
