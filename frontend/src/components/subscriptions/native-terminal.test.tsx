import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { NativeTerminal } from "./native-terminal";

const terminal = vi.hoisted(() => ({
  write: vi.fn(),
  dispose: vi.fn(),
  disposeInput: vi.fn(),
  focus: vi.fn(),
  fit: vi.fn(),
  onInput: undefined as ((data: string) => void) | undefined,
  options: { disableStdin: true },
}));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    cols = 120;
    rows = 32;
    options = terminal.options;
    loadAddon() {}
    open() {}
    resize(cols: number, rows: number) {
      this.cols = cols;
      this.rows = rows;
    }
    focus = terminal.focus;
    write = terminal.write;
    dispose = terminal.dispose;
    onData(callback: (data: string) => void) {
      terminal.onInput = callback;
      return { dispose: terminal.disposeInput };
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit = terminal.fit;
  },
}));
vi.mock("@xterm/addon-web-links", () => ({ WebLinksAddon: class {} }));

class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 0;
  binaryType = "";
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  constructor(
    readonly url: string,
    readonly protocols: string[],
  ) {
    Socket.instances.push(this);
  }
  open() {
    this.readyState = Socket.OPEN;
    this.onopen?.();
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  Socket.instances = [];
  terminal.options.disableStdin = true;
  terminal.onInput = undefined;
  vi.stubGlobal("WebSocket", Socket);
  vi.spyOn(window, "setInterval");
  vi.spyOn(window, "clearInterval");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function mount() {
  const authorize = vi.fn().mockResolvedValue("fresh-ticket");
  const onConnection = vi.fn();
  const onEnd = vi.fn();
  const props = { ticket: "one-use-ticket", authorize, onConnection, onEnd };
  const view = render(<NativeTerminal {...props} />);
  await waitFor(() => expect(Socket.instances).toHaveLength(1));
  const timers = vi.mocked(window.setInterval).mock;
  const renewalIndex = timers.calls.findIndex(([, delay]) => delay === 60_000);
  const refresh = timers.calls[renewalIndex]?.[0];
  const renewal = timers.results[renewalIndex];
  if (typeof refresh !== "function" || renewal?.type !== "return")
    throw new Error("The native terminal did not schedule authorization renewal");
  return {
    ...view,
    props,
    socket: Socket.instances[0]!,
    authorize,
    onConnection,
    onEnd,
    refresh,
    refreshHandle: renewal.value,
  };
}

it("puts the one-use ticket in the subprotocol, keeps bytes in the terminal and sends user input only after connect", async () => {
  const { socket, onConnection } = await mount();
  expect(socket.url).toBe("ws://localhost:8000/native-subscriptions/terminal");
  expect(socket.url).not.toContain("ticket");
  expect(socket.protocols).toEqual(["agenticos-native-v1", "ticket.one-use-ticket"]);
  expect(socket.send).not.toHaveBeenCalled();
  act(() => {
    socket.open();
  });
  expect(onConnection).toHaveBeenLastCalledWith("open");
  expect(terminal.options.disableStdin).toBe(false);
  expect(JSON.parse(socket.send.mock.calls[0]![0])).toEqual({
    type: "resize",
    cols: 120,
    rows: 32,
  });
  act(() => {
    terminal.onInput?.("user-entered-text");
  });
  expect(JSON.parse(socket.send.mock.calls.at(-1)![0])).toEqual({
    type: "input",
    data: "user-entered-text",
  });
  // Native TextEncoder may allocate in the host realm; a browser WebSocket
  // supplies an ArrayBuffer from the page's realm instead.
  const encoded = new TextEncoder().encode("native output");
  const frame = new ArrayBuffer(encoded.byteLength);
  const bytes = new Uint8Array(frame);
  bytes.set(encoded);
  act(() => {
    socket.onmessage?.({ data: frame });
  });
  expect(terminal.write).toHaveBeenCalledWith(bytes);
});

it("renews authorization with a newly minted ticket and cancels renewal on unmount", async () => {
  const { socket, authorize, unmount, refresh, refreshHandle } = await mount();
  act(() => {
    socket.open();
  });
  await act(async () => {
    refresh();
  });
  expect(authorize).toHaveBeenCalledOnce();
  expect(JSON.parse(socket.send.mock.calls.at(-1)![0])).toEqual({
    type: "reauth",
    ticket: "fresh-ticket",
  });
  unmount();
  expect(window.clearInterval).toHaveBeenCalledWith(refreshHandle);
  expect(socket.close).toHaveBeenCalled();
  expect(socket.onmessage).toBeNull();
  expect(terminal.dispose).toHaveBeenCalledOnce();
  expect(terminal.disposeInput).toHaveBeenCalledOnce();
});

it("closes the prior socket when a fresh attachment replaces it", async () => {
  const { socket, props, rerender } = await mount();
  rerender(<NativeTerminal {...props} ticket="replacement-ticket" />);
  await waitFor(() => expect(Socket.instances).toHaveLength(2));
  expect(socket.close).toHaveBeenCalledOnce();
  expect(socket.onmessage).toBeNull();
  expect(Socket.instances[1]!.protocols).toEqual([
    "agenticos-native-v1",
    "ticket.replacement-ticket",
  ]);
});

it("fails closed when reauthorization no longer belongs to the same user and org", async () => {
  const { socket, authorize, onConnection, onEnd, refresh } = await mount();
  authorize.mockResolvedValue(null);
  act(() => {
    socket.open();
  });
  await act(async () => {
    refresh();
  });
  expect(onConnection).toHaveBeenLastCalledWith("error");
  expect(onEnd).toHaveBeenCalledOnce();
  expect(socket.close).toHaveBeenCalled();
  expect(terminal.options.disableStdin).toBe(true);
});

it("ends once on native exit and retains the terminal output for the user", async () => {
  const { socket, onConnection, onEnd, refreshHandle } = await mount();
  act(() => {
    socket.open();
    socket.onmessage?.({ data: JSON.stringify({ type: "authorized" }) });
    socket.onmessage?.({ data: JSON.stringify({ type: "exit", code: 0 }) });
    socket.onclose?.({ code: 1000 });
  });
  expect(onConnection).toHaveBeenLastCalledWith("closed");
  expect(onEnd).toHaveBeenCalledOnce();
  expect(terminal.dispose).not.toHaveBeenCalled();
  expect(window.clearInterval).toHaveBeenCalledWith(refreshHandle);
  expect(terminal.options.disableStdin).toBe(true);
});

it("cleans up an initialized terminal if the socket cannot be constructed", async () => {
  vi.stubGlobal(
    "WebSocket",
    class {
      constructor() {
        throw new Error("invalid endpoint");
      }
    },
  );
  const onConnection = vi.fn();
  const onEnd = vi.fn();
  const { unmount } = render(
    <NativeTerminal
      ticket="ticket"
      authorize={vi.fn()}
      onConnection={onConnection}
      onEnd={onEnd}
    />,
  );
  await waitFor(() => expect(onConnection).toHaveBeenLastCalledWith("error"));
  expect(terminal.dispose).toHaveBeenCalledOnce();
  expect(onEnd).toHaveBeenCalledOnce();
  unmount();
  expect(terminal.dispose).toHaveBeenCalledOnce();
});
