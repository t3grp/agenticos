"use client";

import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { useRoomRouting } from "@/hooks/use-room-routing";
import { getErrorMessage } from "@/lib/api-error";
import { clientId } from "@/lib/ids";
import type { Agent } from "@/types/agents";
import type { LeadPolicy, RoutingProposal } from "@/types/room-routing";

export function RoomLeadPanel({
  room,
  root,
  agents,
}: {
  room: string;
  root: string | null;
  agents: readonly Agent[];
}) {
  const t = useTranslations("chat.roomLead");
  const errors = useTranslations("errors");
  const routing = useRoomRouting(room, root);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const keys = useRef(new Map<string, string>());
  async function decide(proposal: RoutingProposal, accept: boolean) {
    const action = `${proposal.id}:${accept}`;
    const key = keys.current.get(action) ?? clientId();
    keys.current.set(action, key);
    setBusy(true);
    setError(null);
    try {
      await routing.decide(
        proposal,
        accept,
        key,
        accept ? proposal.items.map((item) => item.item_id) : [],
      );
    } catch (failure) {
      if (routing.isCurrent()) setError(getErrorMessage(failure, errors, t("failed")));
    } finally {
      if (routing.isCurrent()) setBusy(false);
    }
  }
  if (routing.isError)
    return (
      <p role="alert" className="text-destructive mb-2 text-xs">
        {getErrorMessage(routing.error, errors, t("failed"))}
      </p>
    );
  const data = routing.data;
  if (!data?.policy) return null;
  return (
    <section aria-label={t("title")} className="mb-3 space-y-2 text-sm">
      {data.policy.can_edit && (
        <LeadPolicyEditor
          key={`${room}:${data.policy.revision}`}
          policy={data.policy}
          agents={agents}
          save={routing.save}
        />
      )}
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      {data.proposals
        .filter((proposal) => proposal.status === "pending")
        .map((proposal) => (
          <article key={proposal.id} className="border-border rounded-xl border p-3">
            <h3 className="mb-2 font-medium">{t("proposal")}</h3>
            <ol className="space-y-2">
              {proposal.items.map((item) => (
                <li key={item.item_id}>
                  <p className="font-medium">{item.title}</p>
                  <p className="text-muted-foreground break-words">{item.expected_result}</p>
                  <p className="text-xs">
                    {t("specialist", {
                      name:
                        agents.find((agent) => agent.id === item.specialist.agent_id)?.name ??
                        item.specialist.agent_id,
                    })}
                  </p>
                  <p className="text-muted-foreground text-xs break-all">
                    {t("reviewDetails", {
                      accountable: item.accountable_user_id,
                      reviewer: item.reviewer_user_id,
                      version: item.specialist.version_id,
                    })}
                  </p>
                  {item.due_at && <p className="text-xs">{t("due", { time: item.due_at })}</p>}
                </li>
              ))}
            </ol>
            {proposal.can_decide && (
              <div className="mt-3 flex flex-wrap gap-2">
                {proposal.can_accept && (
                  <Button size="sm" disabled={busy} onClick={() => void decide(proposal, true)}>
                    {t("accept")}
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void decide(proposal, false)}
                >
                  {t("discard")}
                </Button>
              </div>
            )}
          </article>
        ))}
      {data.tasks.map((task) => (
        <article
          key={task.task_id}
          className="border-border flex flex-wrap items-center justify-between gap-2 rounded-xl border p-3"
        >
          <h3 className="min-w-0 font-medium break-words">{task.title}</h3>
          <p className="text-muted-foreground w-full text-xs break-words">
            {t("ownership", {
              owner: task.assignee_user_id ?? "",
              reviewer: task.reviewer_user_id,
            })}
          </p>
          <span role="status" className="text-muted-foreground text-xs">
            {t(`status.${task.status}`)}
          </span>
        </article>
      ))}
    </section>
  );
}

function LeadPolicyEditor({
  policy,
  agents,
  save,
}: {
  policy: LeadPolicy;
  agents: readonly Agent[];
  save: ReturnType<typeof useRoomRouting>["save"];
}) {
  const t = useTranslations("chat.roomLead");
  const errors = useTranslations("errors");
  const [lead, setLead] = useState(policy.lead?.agent_id ?? "");
  const [specialists, setSpecialists] = useState(policy.specialists.map((pin) => pin.agent_id));
  const [enabled, setEnabled] = useState(policy.enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const available = agents.filter((agent) => agent.current_version_id);
  async function submit() {
    const selected = available.find((agent) => agent.id === lead);
    setBusy(true);
    setError(null);
    try {
      await save({
        expected_revision: policy.revision,
        enabled,
        max_items: 3,
        lead: selected?.current_version_id
          ? {
              agent_id: selected.id,
              version_id:
                selected.id === policy.lead?.agent_id
                  ? policy.lead.version_id
                  : selected.current_version_id,
            }
          : null,
        specialists: available
          .filter((agent) => specialists.includes(agent.id))
          .map((agent) => ({
            agent_id: agent.id,
            version_id:
              policy.specialists.find((pin) => pin.agent_id === agent.id)?.version_id ??
              agent.current_version_id!,
          })),
      });
    } catch (failure) {
      setError(getErrorMessage(failure, errors, t("failed")));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="border-border rounded-xl border p-3">
      <summary className="cursor-pointer font-medium">{t("title")}</summary>
      <form
        className="mt-3 space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          {t("enabled")}
        </label>
        <label className="flex flex-wrap items-center gap-2">
          {t("lead")}
          <select
            className="bg-background min-w-0 rounded border p-2"
            value={lead}
            onChange={(event) => setLead(event.target.value)}
            required={enabled}
          >
            <option value="">{t("none")}</option>
            {available.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </label>
        <fieldset>
          <legend className="mb-2 font-medium">{t("specialists")}</legend>
          <div className="flex flex-wrap gap-3">
            {available.map((agent) => (
              <label key={agent.id} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={specialists.includes(agent.id)}
                  onChange={(event) =>
                    setSpecialists(
                      event.target.checked
                        ? [...specialists, agent.id]
                        : specialists.filter((id) => id !== agent.id),
                    )
                  }
                />
                {agent.name}
              </label>
            ))}
          </div>
        </fieldset>
        {error && (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        )}
        <Button size="sm" type="submit" disabled={busy}>
          {t("save")}
        </Button>
      </form>
    </details>
  );
}
