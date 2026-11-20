import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import messages from "../../../messages/en.json";
import { ChatAgentIdentityAppearance } from "./chat-agent-identity-appearance";
import type { Agent } from "@/types/agents";

const baseAgent = {
  id: "agent-a",
  slug: "agent-a",
  has_avatar: false,
  avatar_color: null,
} as Agent;

function renderAppearance(agent = baseAgent) {
  const onUpload = vi.fn(async (_file: File) => undefined);
  const onRemove = vi.fn(async () => undefined);
  const onColorChange = vi.fn(async (_color: number | null) => undefined);
  const view = render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <ChatAgentIdentityAppearance
        agent={agent}
        disabled={false}
        busy={false}
        onUpload={onUpload}
        onRemove={onRemove}
        onColorChange={onColorChange}
      />
    </NextIntlClientProvider>,
  );
  return { ...view, onUpload, onRemove, onColorChange };
}

describe("agent identity controls in the chat sidebar", () => {
  it("uploads a real image and exposes removal only for an existing picture", async () => {
    const { rerender, onUpload, onRemove } = renderAppearance();
    const image = new File(["image bytes"], "portrait.png", { type: "image/png" });

    await userEvent.upload(screen.getByLabelText("Choose an agent picture"), image);
    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(image));
    expect(screen.queryByRole("button", { name: "Remove picture" })).toBeNull();

    rerender(
      <NextIntlClientProvider locale="en" messages={messages}>
        <ChatAgentIdentityAppearance
          agent={{ ...baseAgent, has_avatar: true }}
          disabled={false}
          busy={false}
          onUpload={onUpload}
          onRemove={onRemove}
          onColorChange={vi.fn(async () => undefined)}
        />
      </NextIntlClientProvider>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Remove picture" }));
    await waitFor(() => expect(onRemove).toHaveBeenCalledOnce());
    expect(onUpload).toHaveBeenCalledOnce();
  });

  it("saves a selected fallback color and resets to automatic", async () => {
    const { onColorChange, onUpload, onRemove, rerender } = renderAppearance();

    await userEvent.click(screen.getByRole("radio", { name: "Colour 4" }));
    expect(onColorChange).toHaveBeenNthCalledWith(1, 4);
    rerender(
      <NextIntlClientProvider locale="en" messages={messages}>
        <ChatAgentIdentityAppearance
          agent={{ ...baseAgent, avatar_color: 4 }}
          disabled={false}
          busy={false}
          onUpload={onUpload}
          onRemove={onRemove}
          onColorChange={onColorChange}
        />
      </NextIntlClientProvider>,
    );

    await userEvent.click(screen.getByRole("radio", { name: "Automatic colour" }));
    await waitFor(() => expect(onColorChange).toHaveBeenNthCalledWith(2, null));
  });

  it("does not manufacture an empty image when no file is selected", async () => {
    const { onUpload } = renderAppearance();

    fireEvent.change(screen.getByLabelText("Choose an agent picture"), {
      target: { files: [] },
    });

    expect(onUpload).not.toHaveBeenCalled();
  });
});
