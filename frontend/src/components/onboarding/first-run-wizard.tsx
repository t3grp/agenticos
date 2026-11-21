"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { ArrowRight, CheckCircle2, LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui";
import { ErrorState, LoadingState } from "@/components/states";
import { useStart } from "@/hooks/use-start";
import { useRouter } from "@/lib/locale-navigation";
import {
  connectApp,
  getAppConnectionCatalog,
  listAppConnections,
} from "@/lib/app-connections-api";
import { exampleChat, START_GOALS, type StartGoal } from "@/lib/onboarding-api";
import { qk } from "@/lib/query-keys";
import { useAuthStore, useOrgStore } from "@/stores";
import { StartSetup } from "./start-setup";

const STEPS = ["connect", "goal", "run", "result"] as const;
type Step = (typeof STEPS)[number];

function parseStep(value: string | undefined): Step | null {
  return (STEPS as readonly string[]).includes(value ?? "") ? (value as Step) : null;
}

/**
 * The guided first run: connect one app, pick a goal, run one agent task,
 * see the result — in about five minutes.
 *
 * The wizard never claims completion on its own. It only offers "Finish" once
 * `GET /me/onboarding` reports `has_result`: a real completed chat run by
 * this person. Dismissing or snoozing hides the offer; it never forces the
 * flow, and the sidebar entry reopens it at any time.
 *
 * Step 1 may be skipped: the OAuth round-trip leaves this page, and the user
 * comes back through the sidebar entry, where the step is derived from live
 * state (an existing connection moves straight to the goal).
 */
export function FirstRunWizard({ initialStep }: { initialStep?: string }) {
  const t = useTranslations("firstRun");
  const tStart = useTranslations("start");
  const router = useRouter();
  const state = useStart();
  const userId = useAuthStore((s) => s.user?.id);
  const orgId = useOrgStore((s) => s.activeOrgId);
  const catalog = useQuery({
    queryKey: qk.appConnections.catalog(userId ?? "", orgId, ""),
    queryFn: () => getAppConnectionCatalog(undefined, ""),
    enabled: !!userId && !!orgId,
  });
  const connections = useQuery({
    queryKey: qk.appConnections.list(userId ?? "", orgId),
    queryFn: () => listAppConnections(),
    enabled: !!userId && !!orgId,
  });
  const [override, setOverride] = useState<Step | null>(null);
  const [goal, setGoal] = useState<StartGoal>("meeting");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const actionRef = useRef<HTMLButtonElement>(null);

  const data = state.data;
  const requested = parseStep(initialStep);
  const derived: Step = !data
    ? "connect"
    : data.has_result
      ? "result"
      : (data.goal ?? null) !== null
        ? "run"
        : (connections.data?.length ?? 0) > 0
          ? "goal"
          : "connect";
  const current = override ?? requested ?? derived;
  const index = STEPS.indexOf(current);

  // While the result step waits, re-read the onboarding state: the run
  // happens in chat, on another page, and this is how the wizard learns it
  // finished. Polling stops the moment a result exists.
  useEffect(() => {
    if (current !== "result" || data?.has_result) return;
    const timer = window.setInterval(() => void state.refetch(), 5000);
    return () => window.clearInterval(timer);
  }, [current, data?.has_result, state.refetch]);

  if (state.isLoading || state.waitingForScope)
    return <LoadingState variant="pending" />;
  if (state.error || !data)
    return (
      <ErrorState
        title={t("loadFailed")}
        cta={{ label: t("retry"), onClick: () => void state.refetch() }}
      />
    );

  const go = (next: Step) => setOverride(next);
  const agent = data.agents[0] ?? data.setup_agent ?? null;
  const chosenGoal = data.goal ?? goal;

  const connect = async (toolkit: string) => {
    setConnecting(true);
    setConnectError(false);
    try {
      const auth = await connectApp({ toolkit });
      window.location.assign(auth.redirect_url);
    } catch {
      setConnectError(true);
      setConnecting(false);
    }
  };

  const chooseGoal = async (next: StartGoal) => {
    setGoal(next);
    setSaveError(false);
    try {
      await state.save.mutateAsync({ goal: next });
      go("run");
    } catch {
      setSaveError(true);
    }
  };

  const snooze = async () => {
    try {
      await state.save.mutateAsync({ dismissed: true });
    } catch {
      // The offer hides on the next load either way; leaving is not blocked.
    }
    router.push("/dashboard");
  };

  const finish = async () => {
    try {
      await state.save.mutateAsync({ completed: true });
    } catch {
      // Completion is server truth; a failed save must not trap the user.
    }
    router.push(
      data.latest_conversation_id ? `/chat?id=${data.latest_conversation_id}` : "/chat",
    );
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6 p-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground text-sm">{t("intro")}</p>
        <ol className="flex flex-wrap gap-2 text-sm" aria-label={t("stepLabel", { current: index + 1, total: STEPS.length })}>
          {STEPS.map((item, position) => (
            <li
              key={item}
              aria-current={item === current ? "step" : undefined}
              className={position === index ? "font-semibold" : "text-muted-foreground"}
            >
              {t(
                item === "connect"
                  ? "stepConnect"
                  : item === "goal"
                    ? "stepGoal"
                    : item === "run"
                      ? "stepRun"
                      : "stepResult",
              )}
              {position < STEPS.length - 1 ? " →" : ""}
            </li>
          ))}
        </ol>
      </header>

      {current === "connect" && (
        <section aria-label={t("connectTitle")} className="space-y-4">
          <h2 className="text-lg font-semibold">{t("connectTitle")}</h2>
          <p className="text-muted-foreground text-sm">{t("connectHint")}</p>
          <ul className="space-y-2">
            {(catalog.data?.apps ?? []).slice(0, 6).map((app) => (
              <li key={app.key}>
                <Button
                  variant="outline"
                  disabled={connecting}
                  onClick={() => void connect(app.toolkit)}
                >
                  {connecting && <LoaderCircle size={14} aria-hidden className="animate-spin" />}
                  {t("connectOpen")} {app.name}
                </Button>
              </li>
            ))}
          </ul>
          {connectError && <p role="alert">{t("connectError")}</p>}
          {connecting && <p aria-live="polite">{t("connecting")}</p>}
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => go("goal")}>
              {t("connectSkip")}
            </Button>
          </div>
        </section>
      )}

      {current === "goal" && (
        <section aria-label={t("goalTitle")} className="space-y-4">
          <h2 className="text-lg font-semibold">{t("goalTitle")}</h2>
          <p className="text-muted-foreground text-sm">{t("goalHint")}</p>
          <ul className="space-y-2">
            {START_GOALS.map((item) => (
              <li key={item}>
                <Button
                  variant="outline"
                  disabled={state.save.isPending}
                  onClick={() => void chooseGoal(item)}
                >
                  {tStart(`examples.${item}.title`)}
                </Button>
                <p className="text-muted-foreground text-sm">
                  {tStart(`examples.${item}.description`)}
                </p>
              </li>
            ))}
          </ul>
          {saveError && <p role="alert">{t("saveFailed")}</p>}
        </section>
      )}

      {current === "run" && (
        <section aria-label={t("runTitle")} className="space-y-4">
          <h2 className="text-lg font-semibold">{t("runTitle")}</h2>
          {agent ? (
            <>
              <p className="text-muted-foreground text-sm">{t("runHint", { agent: agent.name })}</p>
              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() => router.push(exampleChat(agent.id, chosenGoal))}
                >
                  {t("runCta")}
                  <ArrowRight size={14} aria-hidden />
                </Button>
                <Button variant="outline" onClick={() => go("result")}>
                  {t("runCheck")}
                </Button>
              </div>
            </>
          ) : (
            <>
              <p className="text-muted-foreground text-sm">{t("runNoAgent")}</p>
              <p className="text-muted-foreground text-sm">{t("runNoAgentHint")}</p>
              {data.can_prepare && (
                <>
                  <Button ref={actionRef} onClick={() => setSetupOpen(true)}>
                    {t("runSetupModel")}
                  </Button>
                  <StartSetup
                    data={data}
                    open={setupOpen}
                    onOpenChange={setSetupOpen}
                    onReturnFocus={() => actionRef.current?.focus()}
                  />
                </>
              )}
            </>
          )}
        </section>
      )}

      {current === "result" && (
        <section aria-label={t("resultTitle")} className="space-y-4">
          <h2 className="text-lg font-semibold">{t("resultTitle")}</h2>
          {data.has_result && data.latest_conversation_id ? (
            <>
              <p>
                <CheckCircle2 size={16} aria-hidden className="mr-1 inline" />
                {t("resultDone")}
              </p>
              <Link
                href={`/chat?id=${data.latest_conversation_id}`}
                className="text-sm underline"
              >
                {t("resultLink")}
              </Link>
              <div>
                <Button onClick={() => void finish()}>{t("finish")}</Button>
                <p className="text-muted-foreground text-sm">{t("finishHint")}</p>
              </div>
            </>
          ) : (
            <>
              <p aria-live="polite">
                <LoaderCircle size={16} aria-hidden className="mr-1 inline animate-spin" />
                {t("resultWaiting")}
              </p>
              <p className="text-muted-foreground text-sm">{t("resultWaitingHint")}</p>
              <div className="rounded-xl border p-4">
                <h3 className="font-semibold">{t("exampleTitle")}</h3>
                <p className="text-muted-foreground text-sm">{t("exampleHint")}</p>
                <p className="mt-2 text-sm whitespace-pre-line">
                  {tStart(`examples.${chosenGoal}.result`)}
                </p>
              </div>
              <Button variant="outline" onClick={() => go("run")}>
                {t("runCta")}
              </Button>
            </>
          )}
        </section>
      )}

      <footer className="flex flex-wrap items-center gap-2 border-t pt-4">
        <Button
          variant="ghost"
          size="sm"
          disabled={state.save.isPending}
          onClick={() => void snooze()}
        >
          {t("snooze")}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => router.push("/chat")}>
          {t("exit")}
        </Button>
        <Link href="/onboarding" className="ml-auto text-sm underline">
          {t("backToTeam")}
        </Link>
      </footer>
    </div>
  );
}
