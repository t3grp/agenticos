"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import { DisclosureContent } from "@/components/ui/disclosure-content";

/** How long the confirmation is read before it folds, and how long folding takes. */
export const ANSWERED_HOLD_MS = 2500;
export const ANSWERED_FOLD_MS = 200;

export interface Answered {
  id: string;
  title: string;
  approved: boolean;
}

/**
 * Decisions the reader has just made. The queue drops an answered row on its
 * next read, so the confirmation is kept here rather than on the row.
 */
export function useAnswered() {
  const [answered, setAnswered] = useState<Answered[]>([]);
  const record = useCallback((item: Answered) => setAnswered((list) => [...list, item]), []);
  const dismiss = useCallback(
    (id: string) => setAnswered((list) => list.filter((item) => item.id !== id)),
    [],
  );
  return { answered, record, dismiss };
}

/** One answered decision: says what was decided, then folds away. */
export function AnsweredRow({ item, onGone }: { item: Answered; onGone: (id: string) => void }) {
  const t = useTranslations("pages.runs");
  const [folding, setFolding] = useState(false);

  useEffect(() => {
    const fold = setTimeout(() => setFolding(true), ANSWERED_HOLD_MS);
    const gone = setTimeout(() => onGone(item.id), ANSWERED_HOLD_MS + ANSWERED_FOLD_MS);
    return () => {
      clearTimeout(fold);
      clearTimeout(gone);
    };
  }, [item.id, onGone]);

  const Icon = item.approved ? CheckCircle2 : XCircle;
  return (
    <li>
      <DisclosureContent open={!folding}>
        <p role="status" className="flex items-center gap-2 px-4 py-3 text-sm">
          <Icon
            className={
              item.approved ? "text-success size-4 shrink-0" : "text-destructive size-4 shrink-0"
            }
            aria-hidden
          />
          <span className="font-medium">
            {item.approved ? t("decisionApproved") : t("decisionRejected")}
          </span>
          <span className="text-muted-foreground min-w-0 truncate">{item.title}</span>
        </p>
      </DisclosureContent>
    </li>
  );
}
