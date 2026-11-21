"use client";

import { useTranslations } from "next-intl";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DIALOG_FORM, DIALOG_SCROLL } from "@/lib/dialog-sizes";
import type { RoomAutoRoutingPolicyController } from "@/hooks/use-room-auto-routing-policy";
import { RoomAutoRoutingSettingsForm } from "./room-auto-routing-settings-form";

export function RoomAutoRoutingSettingsDialog({
  conversationId,
  controller,
  onClose,
}: {
  conversationId: string;
  controller: RoomAutoRoutingPolicyController;
  onClose: () => void;
}) {
  const t = useTranslations("chat.roomAutoRouting");
  const policy = controller.policy;
  if (policy === undefined) return null;

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !controller.isSaving) onClose();
      }}
    >
      <DialogContent
        className={`${DIALOG_FORM} ${DIALOG_SCROLL}`}
        dismissible={!controller.isSaving}
      >
        <DialogHeader>
          <DialogTitle>{t("configureTitle")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <RoomAutoRoutingSettingsForm
          key={`${controller.scopeKey}:${policy.revision}`}
          conversationId={conversationId}
          policy={policy}
          controller={controller}
          onClose={onClose}
        />
      </DialogContent>
    </Dialog>
  );
}
