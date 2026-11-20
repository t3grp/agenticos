"use client";

import { useId, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import {
  Button,
  FormField,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
} from "@/components/ui";
import { useModelProviders } from "@/hooks/use-model-providers";
import { useRoomAutoRoutingCandidates } from "@/hooks/use-room-auto-routing-candidates";
import { getErrorMessage } from "@/lib/api-error";
import { usableForAgents } from "@/lib/model-profiles";
import type { RoomAutoRoutingPolicyRead } from "@/types/room-auto-routing";
import { RoomAutoRoutingAgentField } from "./room-auto-routing-agent-field";
import { RoomAutoRoutingModelField } from "./room-auto-routing-model-field";
import type { RoomAutoRoutingPolicyController } from "@/hooks/use-room-auto-routing-policy";

export function RoomAutoRoutingSettingsForm({
  conversationId,
  policy,
  controller,
  onClose,
}: {
  conversationId: string;
  policy: RoomAutoRoutingPolicyRead;
  controller: RoomAutoRoutingPolicyController;
  onClose: () => void;
}) {
  const t = useTranslations("chat.roomAutoRouting");
  const errors = useTranslations("errors");
  const tc = useTranslations("common");
  const id = useId();
  const [enabled, setEnabled] = useState(policy.enabled);
  const [agentId, setAgentId] = useState(policy.router_agent_id);
  const [modelProfileId, setModelProfileId] = useState(policy.model_profile_id);
  const [quietWindow, setQuietWindow] = useState(String(policy.quiet_window_ms));
  const [maxResponders, setMaxResponders] = useState(policy.max_responders);
  const candidates = useRoomAutoRoutingCandidates(conversationId);
  const models = useModelProviders();
  const usableProfiles = models.profiles.filter(
    (profile) => profile.usage_scope === "organization" && usableForAgents(profile),
  );
  const quietValue = Number(quietWindow);
  const quietIsValid =
    quietWindow.trim() !== "" &&
    Number.isInteger(quietValue) &&
    quietValue >= 0 &&
    quietValue <= 10_000;
  const maxRespondersIsValid =
    Number.isInteger(maxResponders) && maxResponders >= 1 && maxResponders <= 4;
  const agentIsValid = candidates.candidates.some((candidate) => candidate.id === agentId);
  const modelIsValid = usableProfiles.some((profile) => profile.id === modelProfileId);
  const hasAgent = agentId !== null;
  const hasModel = modelProfileId !== null;
  const selectionIsPaired = hasAgent === hasModel;
  const selectionIsUnchanged =
    agentId === policy.router_agent_id && modelProfileId === policy.model_profile_id;
  const supportsReady =
    !candidates.isLoading &&
    !candidates.error &&
    models.profilesStatus === "loaded" &&
    !models.isLoading;
  const selectedPairIsUsable =
    supportsReady && agentIsValid && modelIsValid && controller.isCurrent();
  const selectionIsValid =
    selectionIsPaired &&
    (enabled
      ? hasAgent && selectedPairIsUsable
      : !hasAgent || selectionIsUnchanged || selectedPairIsUsable);
  const busy = controller.isSaving;
  const disabled = !policy.can_edit;
  const canSave =
    !busy &&
    !controller.isConflict &&
    !disabled &&
    quietIsValid &&
    maxRespondersIsValid &&
    selectionIsValid;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    try {
      const saved = await controller.savePolicy({
        enabled,
        router_agent_id: agentId,
        model_profile_id: modelProfileId,
        quiet_window_ms: quietValue,
        max_responders: maxResponders,
      });
      if (saved !== null && controller.isCurrent()) onClose();
    } catch {
      // The hook keeps a scoped error for this form to render.
    }
  }

  const saveError = controller.saveError
    ? controller.isConflict
      ? t("policyChanged")
      : getErrorMessage(controller.saveError, errors, t("saveFailed"))
    : null;

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-5">
      {controller.error && (
        <div role="alert" className="flex items-center gap-2 text-sm">
          <span className="text-destructive">{t("policyUnavailable")}</span>
          <Button type="button" variant="ghost" size="sm" onClick={() => void controller.refetch()}>
            {tc("retry")}
          </Button>
        </div>
      )}
      {busy ? (
        <p role="status" className="text-muted-foreground text-sm">
          {t("saving")}
        </p>
      ) : controller.isConflict ? null : (
        <fieldset disabled={disabled} className="space-y-5">
          <label className="flex items-center justify-between gap-3" htmlFor={`${id}-enabled`}>
            <span className="text-sm font-medium">{t("enabled")}</span>
            <Switch id={`${id}-enabled`} checked={enabled} onCheckedChange={setEnabled} />
          </label>
          <p className="text-muted-foreground text-xs">{t("optionalOffByDefault")}</p>
          <RoomAutoRoutingAgentField
            candidates={candidates.candidates}
            value={agentId}
            disabled={false}
            loading={candidates.isLoading}
            failed={Boolean(candidates.error)}
            onRetry={() => void candidates.retry()}
            onChange={setAgentId}
          />
          <RoomAutoRoutingModelField
            profiles={models.profiles}
            catalog={models.catalog}
            status={models.profilesStatus}
            value={modelProfileId}
            disabled={false}
            onRetry={() => void models.refetchProfiles()}
            onChange={setModelProfileId}
          />
          {!selectionIsPaired && (
            <p role="alert" className="text-destructive text-xs">
              {t("selectionPairRequired")}
            </p>
          )}
          <FormField label={t("quietWindow")} htmlFor={`${id}-quiet-window`}>
            <Input
              id={`${id}-quiet-window`}
              type="number"
              min={0}
              max={10_000}
              step={100}
              value={quietWindow}
              onChange={(event) => setQuietWindow(event.target.value)}
              aria-invalid={!quietIsValid}
            />
          </FormField>
          <p className="text-muted-foreground text-xs">{t("quietWindowHint")}</p>
          <FormField label={t("maxResponders")} htmlFor={`${id}-max-responders`}>
            <Select
              value={String(maxResponders)}
              onValueChange={(value) => setMaxResponders(Number(value))}
            >
              <SelectTrigger id={`${id}-max-responders`}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[1, 2, 3, 4].map((value) => (
                  <SelectItem key={value} value={String(value)}>
                    {value}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>
        </fieldset>
      )}
      {saveError && (
        <p role="alert" className="text-destructive text-sm">
          {saveError}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
          {t("cancel")}
        </Button>
        {controller.isConflict ? (
          <Button type="button" onClick={() => void controller.refetch()}>
            {t("reloadPolicy")}
          </Button>
        ) : (
          <Button type="submit" disabled={!canSave}>
            {t(busy ? "saving" : "save")}
          </Button>
        )}
      </div>
    </form>
  );
}
