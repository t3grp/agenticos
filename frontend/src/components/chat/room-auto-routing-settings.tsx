"use client";

import { Loader2, Settings2 } from "lucide-react";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { useRoomAutoRoutingPolicy } from "@/hooks/use-room-auto-routing-policy";
import { RoomAutoRoutingSettingsDialog } from "./room-auto-routing-settings-dialog";

/** Optional automatic reply routing settings for one shared room. */
export function RoomAutoRoutingSettings({ conversationId }: { conversationId: string }) {
  const controller = useRoomAutoRoutingPolicy(conversationId);
  return (
    <RoomAutoRoutingSettingsScope
      key={controller.scopeKey}
      conversationId={conversationId}
      controller={controller}
    />
  );
}

function RoomAutoRoutingSettingsScope({
  conversationId,
  controller,
}: {
  conversationId: string;
  controller: ReturnType<typeof useRoomAutoRoutingPolicy>;
}) {
  const t = useTranslations("chat.roomAutoRouting");
  const tc = useTranslations("common");
  const [open, setOpen] = useState(false);
  const openForCurrentScope = open && controller.isCurrent();
  const policy = controller.policy;

  return (
    <section
      aria-labelledby="room-auto-routing-heading"
      className="space-y-2 rounded-lg border p-3"
    >
      <h3 id="room-auto-routing-heading" className="text-sm font-medium">
        {t("title")}
      </h3>
      <p className="text-muted-foreground text-xs">{t("description")}</p>
      {controller.isLoading && (
        <p role="status" className="text-muted-foreground flex items-center gap-2 text-sm">
          <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
          {tc("loading")}
        </p>
      )}
      {controller.error && (
        <div role="alert" className="flex items-center gap-2 text-sm">
          <span className="text-destructive">{t("policyUnavailable")}</span>
          <Button type="button" variant="ghost" size="sm" onClick={() => void controller.refetch()}>
            {tc("retry")}
          </Button>
        </div>
      )}
      <div className="flex items-center justify-between gap-3">
        {policy && (
          <span className="text-muted-foreground text-sm">
            {t(policy.enabled ? "statusEnabled" : "statusDisabled")}
          </span>
        )}
        {policy?.can_edit !== false && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={policy?.can_edit !== true || controller.isSaving || !controller.isCurrent()}
            data-tour="room-auto-routing-settings"
            onClick={() => {
              if (policy?.can_edit === true && controller.isCurrent()) setOpen(true);
            }}
          >
            <Settings2 className="size-3.5" aria-hidden />
            {t("configure")}
          </Button>
        )}
      </div>
      {openForCurrentScope && (
        <RoomAutoRoutingSettingsDialog
          key={controller.scopeKey}
          conversationId={conversationId}
          controller={controller}
          onClose={() => setOpen(false)}
        />
      )}
    </section>
  );
}
