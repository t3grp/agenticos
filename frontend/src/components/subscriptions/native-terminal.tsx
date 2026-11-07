"use client";

import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";

import { usePublicConfig } from "@/components/public-config/public-config-provider";
import { nativeInputChunks, nativeTerminalSize } from "@/lib/native-terminal-protocol";
import "@xterm/xterm/css/xterm.css";

export type TerminalConnection = "connecting" | "open" | "closed" | "error";

interface NativeTerminalProps {
  ticket: string;
  authorize: () => Promise<string | null>;
  onConnection: (state: TerminalConnection) => void;
  onEnd: () => void;
}

/** Native terminal bytes stay in this component, outside query caches and logs. */
export function NativeTerminal({ ticket, authorize, onConnection, onEnd }: NativeTerminalProps) {
  const t = useTranslations("nativeSubscriptions");
  const host = useRef<HTMLDivElement>(null);
  const { wsUrl } = usePublicConfig();
  useEffect(() => {
    let cancelled = false;
    const disposables: (() => void)[] = [];
    const dispose = () => {
      for (const cleanup of disposables.splice(0).reverse()) cleanup();
    };
    onConnection("connecting");
    void (async () => {
      const [{ Terminal }, { FitAddon }, { WebLinksAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
        import("@xterm/addon-web-links"),
      ]);
      if (cancelled || !host.current) return;
      const terminal = new Terminal({
        fontSize: 13,
        cursorBlink: true,
        scrollback: 1000,
        disableStdin: true,
        screenReaderMode: true,
        theme: { background: "#101114", foreground: "#e8e9ed" },
      });
      disposables.push(() => terminal.dispose());
      const fit = new FitAddon();
      terminal.loadAddon(fit);
      terminal.loadAddon(
        new WebLinksAddon((event, address) => {
          event.preventDefault();
          const url = new URL(address);
          if (url.protocol === "https:" || url.protocol === "http:")
            window.open(url.href, "_blank", "noopener,noreferrer");
        }),
      );
      terminal.open(host.current);
      fit.fit();
      const socket = new WebSocket(`${wsUrl}/native-subscriptions/terminal`, [
        "agenticos-native-v1",
        `ticket.${ticket}`,
      ]);
      disposables.push(() => {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        socket.close();
      });
      socket.binaryType = "arraybuffer";
      let ended = false;
      let authorizing = false;
      let refresh: number | undefined;
      const stopRenewal = () => {
        if (refresh !== undefined) window.clearInterval(refresh);
        refresh = undefined;
      };
      const send = (value: object) => {
        if (!ended && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value));
      };
      const resize = () => {
        fit.fit();
        const size = nativeTerminalSize(terminal.cols, terminal.rows);
        if (size.cols !== terminal.cols || size.rows !== terminal.rows)
          terminal.resize(size.cols, size.rows);
        send({ type: "resize", ...size });
      };
      const input = terminal.onData((text) => {
        for (const data of nativeInputChunks(text)) send({ type: "input", data });
      });
      disposables.push(() => input.dispose());
      const observer = new ResizeObserver(resize);
      disposables.push(() => observer.disconnect());
      observer.observe(host.current);
      const finish = (state: TerminalConnection) => {
        if (ended) return;
        ended = true;
        stopRenewal();
        terminal.options.disableStdin = true;
        onConnection(state);
        onEnd();
      };
      socket.onopen = () => {
        terminal.options.disableStdin = false;
        onConnection("open");
        resize();
        terminal.focus();
      };
      socket.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data instanceof ArrayBuffer) terminal.write(new Uint8Array(event.data));
        else if (typeof event.data === "string") {
          try {
            const message: unknown = JSON.parse(event.data);
            if (typeof message !== "object" || message === null || !("type" in message)) return;
            if (message.type === "exit") finish("closed");
            if (message.type === "error") finish("error");
          } catch {
            finish("error");
            socket.close();
          }
        }
      };
      socket.onerror = () => finish("error");
      socket.onclose = (event) => finish(event.code === 1000 ? "closed" : "error");
      refresh = window.setInterval(() => {
        if (authorizing || ended || socket.readyState !== WebSocket.OPEN) return;
        authorizing = true;
        void authorize()
          .then((fresh) => {
            if (cancelled || ended) return;
            if (fresh) send({ type: "reauth", ticket: fresh });
            else {
              finish("error");
              socket.close();
            }
          })
          .catch(() => {
            if (!cancelled) {
              finish("error");
              socket.close();
            }
          })
          .finally(() => {
            authorizing = false;
          });
      }, 60_000);
      disposables.push(stopRenewal);
    })().catch(() => {
      dispose();
      if (!cancelled) {
        onConnection("error");
        onEnd();
      }
    });
    return () => {
      cancelled = true;
      dispose();
    };
  }, [ticket, authorize, wsUrl, onConnection, onEnd]);
  return (
    <div
      ref={host}
      className="h-[min(65vh,40rem)] min-h-80 overflow-auto rounded-lg bg-[#101114] p-3"
      aria-label={t("terminalLabel")}
      data-tour="subscription-terminal"
    />
  );
}
