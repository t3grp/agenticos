"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { usePermissions } from "@/hooks/use-permissions";
import { usePublicConfig } from "@/components/public-config/public-config-provider";
import {
  discardBrowserTeaching,
  getBrowserTeaching,
  proposeBrowserTeaching,
  reviewBrowserTeaching,
  setBrowserTeaching,
  startBrowserTeaching,
  stopBrowserTeaching,
  type BrowserTeaching,
} from "@/lib/browser-teachings-api";
import type { Browse } from "@/lib/browse";
import { useAuthStore, useOrgStore } from "@/stores";
import { Perm } from "@/types/permissions";

type BrowserTeachingControlsProps = {
  browse: Browse;
  canControl: boolean;
  leaseId: string | null;
};

/** Explicit controls for a redacted demonstration on the held managed browser. */
export function BrowserTeachingControls(props: BrowserTeachingControlsProps) {
  const userId = useAuthStore((state) => state.user?.id);
  const organizationId = useOrgStore((state) => state.activeOrgId);
  return (
    <BrowserTeachingControlsSession
      key={`${userId ?? ""}:${organizationId ?? ""}`}
      userId={userId}
      {...props}
    />
  );
}

function BrowserTeachingControlsSession({
  browse,
  canControl,
  leaseId,
  userId,
}: BrowserTeachingControlsProps & { userId: string | undefined }) {
  const t = useTranslations("chat");
  const canEditSkills = usePermissions().can(Perm.skillsEdit);
  const browserTeachingEnabled = usePublicConfig().browserTeachingEnabled;
  const holding = Boolean(userId && browse.heldById === userId);
  const [teaching, setTeaching] = useState<BrowserTeaching | null>(null);
  const [name, setName] = useState("");
  const [parameters, setParameters] = useState<{ event_id: string; name: string; label: string }[]>(
    [],
  );
  const [stepLabels, setStepLabels] = useState<
    {
      event_id: string;
      sequence: number;
      reviewed_label: string;
    }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  const currentTeaching = teaching;
  const canStart = browserTeachingEnabled && canControl && holding && leaseId !== null;

  const run = async (action: () => Promise<BrowserTeaching | void>) => {
    const requestUserId = userId;
    setBusy(true);
    setError(false);
    try {
      const result = await action();
      if (useAuthStore.getState().user?.id === requestUserId && result) setTeaching(result);
    } catch {
      if (useAuthStore.getState().user?.id === requestUserId) setError(true);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!currentTeaching || currentTeaching.status !== "recording") return;
    const requestUserId = userId;
    const timer = window.setInterval(() => {
      void getBrowserTeaching(currentTeaching.id)
        .then((next) => {
          if (useAuthStore.getState().user?.id === requestUserId) setTeaching(next);
        })
        .catch(() => {
          if (useAuthStore.getState().user?.id === requestUserId) setError(true);
        });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [currentTeaching?.id, currentTeaching?.status, userId]);

  if (!browse.runId || browse.nativeId || !canEditSkills || (!currentTeaching && !canStart))
    return null;

  const isRecording = currentTeaching?.status === "recording";
  const isPaused = currentTeaching?.status === "paused";
  const isReview = currentTeaching?.status === "review";

  return (
    <section
      className="border-border bg-card space-y-3 border-b px-3 py-3"
      aria-label={t("browserTeachingTitle")}
    >
      {!currentTeaching && canStart && (
        <button
          type="button"
          data-tour="browser-teach"
          disabled={busy}
          onClick={() =>
            run(async () => {
              const next = await startBrowserTeaching(
                browse.runId!,
                browse.callId,
                leaseId,
                crypto.randomUUID(),
              );
              setName("");
              setParameters([]);
              setStepLabels([]);
              return next;
            })
          }
          className="rounded-md border px-3 py-2 text-sm"
        >
          {t("browserTeachingStart")}
        </button>
      )}
      {currentTeaching && (isRecording || isPaused) && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">
            {isRecording ? t("browserTeachingRecording") : t("browserTeachingPaused")}
          </span>
          <span className="text-muted-foreground text-xs">
            {t("browserTeachingCount", { count: currentTeaching.steps.length })}
          </span>
          {isRecording ? (
            <button
              type="button"
              disabled={busy || !canControl || !holding}
              onClick={() =>
                run(() => setBrowserTeaching(currentTeaching.id, "pause", currentTeaching.revision))
              }
              className="rounded-md border px-3 py-2 text-sm"
            >
              {t("browserTeachingPause")}
            </button>
          ) : !browserTeachingEnabled ? (
            <span className="text-muted-foreground text-sm">{t("browserTeachingDisabled")}</span>
          ) : currentTeaching.lease_id === leaseId ? (
            <button
              type="button"
              disabled={busy || !canControl || !holding}
              onClick={() =>
                run(() =>
                  setBrowserTeaching(currentTeaching.id, "resume", currentTeaching.revision),
                )
              }
              className="rounded-md border px-3 py-2 text-sm"
            >
              {t("browserTeachingResume")}
            </button>
          ) : (
            <>
              <span className="text-muted-foreground text-sm">
                {t("browserTeachingLeaseEnded")}
              </span>
              {canStart && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      const next = await startBrowserTeaching(
                        browse.runId!,
                        browse.callId,
                        leaseId,
                        crypto.randomUUID(),
                      );
                      setParameters([]);
                      setStepLabels([]);
                      setName("");
                      return next;
                    })
                  }
                  className="rounded-md border px-3 py-2 text-sm"
                >
                  {t("browserTeachingNew")}
                </button>
              )}
            </>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(async () => {
                const stopped = await stopBrowserTeaching(currentTeaching.id);
                setParameters(
                  stopped.steps
                    .filter((step) => step.action === "input_parameter")
                    .map((step, index) => ({
                      event_id: step.event_id,
                      name: step.parameter ?? `input_${index + 1}`,
                      label: t("browserTeachingParameter", { number: index + 1 }),
                    })),
                );
                setStepLabels(
                  stopped.steps
                    .filter((step) => step.action === "click")
                    .map((step) => ({
                      event_id: step.event_id,
                      sequence: step.sequence,
                      reviewed_label: step.reviewed_label ?? "",
                    })),
                );
                return stopped;
              })
            }
            className="rounded-md border px-3 py-2 text-sm"
          >
            {t("browserTeachingStop")}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(async () => {
                await discardBrowserTeaching(currentTeaching.id);
                setTeaching(null);
              })
            }
            className="rounded-md border px-3 py-2 text-sm"
          >
            {t("browserTeachingDiscard")}
          </button>
        </div>
      )}
      {isReview && currentTeaching && (
        <div className="space-y-3">
          <ol className="list-inside list-decimal space-y-1 text-sm">
            {currentTeaching.steps.map((step) => (
              <li key={step.event_id}>
                {t("browserTeachingStep", {
                  action: step.action,
                  role: step.target_role ?? t("browserTeachingTarget"),
                })}
              </li>
            ))}
          </ol>
          {parameters.map((parameter, index) => (
            <div key={index} className="space-y-2">
              <label className="block text-sm">
                <span className="mb-1 block">
                  {t("browserTeachingParameterName", { number: index + 1 })}
                </span>
                <input
                  value={parameter.name}
                  maxLength={40}
                  onChange={(event) =>
                    setParameters((current) =>
                      current.map((item, i) =>
                        i === index ? { ...item, name: event.target.value } : item,
                      ),
                    )
                  }
                  className="bg-background w-full rounded-md border px-3 py-2"
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block">
                  {t("browserTeachingParameterLabel", { number: index + 1 })}
                </span>
                <input
                  value={parameter.label}
                  maxLength={64}
                  onChange={(event) =>
                    setParameters((current) =>
                      current.map((item, i) =>
                        i === index ? { ...item, label: event.target.value } : item,
                      ),
                    )
                  }
                  className="bg-background w-full rounded-md border px-3 py-2"
                />
              </label>
            </div>
          ))}
          {stepLabels.map((step) => (
            <label className="block text-sm" key={step.event_id}>
              <span className="mb-1 block">
                {t("browserTeachingStepLabel", { number: step.sequence + 1 })}
              </span>
              <input
                value={step.reviewed_label}
                maxLength={64}
                onChange={(event) =>
                  setStepLabels((current) =>
                    current.map((item) =>
                      item.event_id === step.event_id
                        ? { ...item, reviewed_label: event.target.value }
                        : item,
                    ),
                  )
                }
                className="bg-background w-full rounded-md border px-3 py-2"
              />
            </label>
          ))}
          <label className="block text-sm">
            <span className="mb-1 block">{t("browserTeachingSkillName")}</span>
            <input
              value={name}
              maxLength={64}
              onChange={(event) => setName(event.target.value)}
              className="bg-background w-full rounded-md border px-3 py-2"
            />
          </label>
          <div className="flex flex-wrap gap-2">
            {browserTeachingEnabled ? (
              <button
                type="button"
                disabled={
                  busy || !name.trim() || stepLabels.some((step) => !step.reviewed_label.trim())
                }
                onClick={() =>
                  run(async () => {
                    const reviewed = await reviewBrowserTeaching(
                      currentTeaching.id,
                      currentTeaching.revision,
                      stepLabels.map(({ event_id, reviewed_label }) => ({
                        event_id,
                        reviewed_label,
                      })),
                      parameters,
                    );
                    setTeaching(reviewed);
                    return proposeBrowserTeaching(reviewed.id, name.trim(), "");
                  })
                }
                className="rounded-md border px-3 py-2 text-sm"
              >
                {t("browserTeachingPropose")}
              </button>
            ) : (
              <p className="text-muted-foreground text-sm">{t("browserTeachingDisabled")}</p>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await discardBrowserTeaching(currentTeaching.id);
                  setTeaching(null);
                })
              }
              className="rounded-md border px-3 py-2 text-sm"
            >
              {t("browserTeachingDiscard")}
            </button>
          </div>
        </div>
      )}
      {currentTeaching?.status === "proposed" && (
        <p className="text-sm">{t("browserTeachingProposalReady")}</p>
      )}
      {isPaused && currentTeaching?.pause_reason && (
        <p className="text-muted-foreground text-sm">
          {currentTeaching.pause_reason === "event_limit"
            ? t("browserTeachingLimitEvents")
            : currentTeaching.pause_reason === "size_limit"
              ? t("browserTeachingLimitSize")
              : currentTeaching.pause_reason === "duration_limit"
                ? t("browserTeachingLimitDuration")
                : currentTeaching.pause_reason === "lease_ended" ||
                    currentTeaching.pause_reason === "lease_lost"
                  ? t("browserTeachingLeaseEnded")
                  : currentTeaching.pause_reason === "browser_finished"
                    ? t("browserTeachingRunEnded")
                    : currentTeaching.pause_reason === "origin_changed"
                      ? t("browserTeachingOriginChanged")
                      : currentTeaching.pause_reason === "feature_disabled"
                        ? t("browserTeachingDisabled")
                        : t("browserTeachingPaused")}
        </p>
      )}
      {currentTeaching?.status === "expired" && (
        <button
          type="button"
          onClick={() => setTeaching(null)}
          className="rounded-md border px-3 py-2 text-sm"
        >
          {t("browserTeachingNew")}
        </button>
      )}
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {t("browserTeachingError")}
        </p>
      )}
      <p className="text-muted-foreground text-xs">{t("browserTeachingPrivacy")}</p>
    </section>
  );
}
