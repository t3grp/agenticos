"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui";
import { ROUTES } from "@/lib/constants";
import { Perm, type Permission } from "@/types/permissions";

/**
 * The controls: what a person can start from here, as buttons - no question,
 * no prompt. Each button is gated on the permission its destination asks, and
 * a caller who may start nothing sees no row at all.
 *
 * There is no app-builder form in the product: an artifact is written by an
 * agent's run, so "Build an app" starts a chat, where the building happens.
 */
export function CommandActions({ can }: { can: (permission: Permission) => boolean }) {
  const t = useTranslations("dashboard.commandCenter.actions");
  const tActions = useTranslations("dashboard.actions");
  const showChat = can(Perm.agentsRun);
  const showWorkflow = can(Perm.workflowsView);
  const showConnect = can(Perm.connectionsView);
  if (!showChat && !showWorkflow && !showConnect) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {showChat ? (
        <>
          <Button asChild>
            <Link href={ROUTES.CHAT}>{tActions("newChat")}</Link>
          </Button>
          <Button asChild variant="outline">
            <Link href={ROUTES.CHAT}>{t("buildApp")}</Link>
          </Button>
        </>
      ) : null}
      {showWorkflow ? (
        <Button asChild variant="outline">
          <Link href={ROUTES.WORKFLOWS}>{t("startWorkflow")}</Link>
        </Button>
      ) : null}
      {showConnect ? (
        <Button asChild variant="outline">
          <Link href={ROUTES.CONNECTORS}>{t("connectService")}</Link>
        </Button>
      ) : null}
    </div>
  );
}
