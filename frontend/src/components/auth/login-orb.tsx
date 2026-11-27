"use client";

import { useState, useSyncExternalStore, type CSSProperties } from "react";
import { useTranslations } from "next-intl";

import { StatusOrb } from "@/components/ui/status-orb";

const ACCENTS = ["var(--brand-300)", "var(--brand-400)", "var(--brand-500)", "var(--brand-600)"];
const MIN_SIZE = 56;
const MAX_SIZE = 72;

interface Pose {
  size: number;
  x: number;
  y: number;
  accent: string;
}

function randomPose(): Pose {
  return {
    size: MIN_SIZE + Math.round(Math.random() * (MAX_SIZE - MIN_SIZE)),
    x: 20 + Math.round(Math.random() * 60),
    y: 20 + Math.round(Math.random() * 40),
    accent: ACCENTS[Math.floor(Math.random() * ACCENTS.length)]!,
  };
}

const subscribe = () => () => {};
const onServer = () => null;

/** The sign-in orb, drawn at random in the browser so the server markup never disagrees. */
export function LoginOrb() {
  const t = useTranslations("auth");
  const [draw] = useState(() => {
    let pose: Pose | undefined;
    return () => (pose ??= randomPose());
  });
  const pose = useSyncExternalStore<Pose | null>(subscribe, draw, onServer);

  return (
    <div className="mb-6 flex items-center gap-3" style={{ minHeight: MAX_SIZE }}>
      {pose && (
        <>
          <StatusOrb
            state="idle"
            size={64}
            style={
              {
                width: pose.size,
                height: pose.size,
                backgroundColor: pose.accent,
                "--orb-x": `${pose.x}%`,
                "--orb-y": `${pose.y}%`,
              } as CSSProperties
            }
          />
          <p className="text-muted-foreground text-sm">{t("orbReady")}</p>
        </>
      )}
    </div>
  );
}
