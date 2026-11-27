"use client";

import { useEffect, useRef, type CSSProperties } from "react";

import { cn } from "@/lib/utils";
import styles from "./status-orb.module.css";

export type OrbState = "idle" | "working" | "waiting" | "done" | "failed";

/** How long an activity must last before it moves, so a quick one never flickers. */
export const ORB_ANIMATION_DELAY_MS = 2000;
/** How many orbs on one page may move at once; the rest stay still until one stops. */
export const ORB_ANIMATION_LIMIT = 3;

const STATE_COLOR: Record<OrbState, string> = {
  idle: "bg-muted-foreground/50",
  working: "bg-brand",
  waiting: "bg-warning",
  done: "bg-success",
  failed: "bg-destructive",
};

let moving = 0;
const queued: Array<() => void> = [];

/** Take a motion slot now or when one frees; returns how to give it back. */
function claimSlot(start: () => void): () => void {
  let held = false;
  const take = () => {
    held = true;
    moving += 1;
    start();
  };
  if (moving < ORB_ANIMATION_LIMIT) take();
  else queued.push(take);
  return () => {
    if (!held) {
      queued.splice(queued.indexOf(take), 1);
      return;
    }
    moving -= 1;
    queued.shift()?.();
  };
}

/**
 * An activity cue. Decorative: the caller always writes the status in words beside it.
 *
 * Only `working` and `waiting` move, and only after the delay, while on screen,
 * within the page's limit, and when the reader has not asked for reduced motion.
 */
export function StatusOrb({
  state,
  size = 20,
  className,
  style,
}: {
  state: OrbState;
  size?: 20 | 64;
  className?: string;
  style?: CSSProperties;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const active = state === "working" || state === "waiting";

  useEffect(() => {
    const orb = ref.current!;
    if (!active) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let release: (() => void) | undefined;
    let ready = false;
    let visible = false;
    const stop = () => {
      release?.();
      release = undefined;
      delete orb.dataset.animate;
    };
    const sync = () => {
      if (!ready || !visible) stop();
      else release ??= claimSlot(() => (orb.dataset.animate = "true"));
    };
    const timer = setTimeout(() => {
      ready = true;
      sync();
    }, ORB_ANIMATION_DELAY_MS);
    const observer = new IntersectionObserver((entries) => {
      visible = entries[entries.length - 1]!.isIntersecting;
      sync();
    });
    observer.observe(orb);
    return () => {
      clearTimeout(timer);
      observer.disconnect();
      stop();
    };
  }, [active]);

  return (
    <span
      ref={ref}
      aria-hidden
      data-state={state}
      className={cn(
        styles.orb,
        "inline-block shrink-0 rounded-full",
        STATE_COLOR[state],
        className,
      )}
      style={{ width: size, height: size, ...style }}
    />
  );
}
