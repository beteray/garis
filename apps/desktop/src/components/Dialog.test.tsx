/**
 * What a modal owes the person using it, especially without a mouse.
 *
 * Each of these is a way an overlay can strand someone: focus left behind the
 * dialog, focus lost when it closes, Escape doing nothing, or a backdrop that
 * has finished fading and is still eating clicks. They were all reachable in
 * this app before there was one implementation of a dialog.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Confirm, Dialog } from "./Dialog";

afterEach(() => {
  document.documentElement.dataset.motion = "";
});

/** A page with something to focus, and a dialog opened from it. */
function Page({ modal = true }: { modal?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen(true)}>Otwórz</button>
      <button>Za oknem</button>
      <Dialog
        open={open}
        modal={modal}
        onClose={() => setOpen(false)}
        title="Pytanie"
        description="Opis."
        footer={<button onClick={() => setOpen(false)}>Zamknij</button>}
      >
        <input aria-label="Pole" />
      </Dialog>
    </div>
  );
}

describe("opening and closing", () => {
  it("is not in the document until it is opened", async () => {
    render(<Page />);
    expect(screen.queryByRole("dialog")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    expect(await screen.findByRole("dialog")).toHaveAccessibleName("Pytanie");
  });

  it("describes itself from its own text, not from a label somewhere else", async () => {
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    expect(screen.getByRole("dialog")).toHaveAccessibleDescription("Opis.");
  });

  it("closes on Escape", async () => {
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    await screen.findByRole("dialog");

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("leaves no backdrop behind after it has gone", async () => {
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    await screen.findByRole("dialog");
    await userEvent.keyboard("{Escape}");

    // The failure this guards: a scrim that finished fading and stayed in the
    // document, invisible, swallowing every click meant for the window.
    await waitFor(() => expect(document.querySelector(".dialog-layer")).toBeNull());
  });
});

describe("the keyboard", () => {
  it("puts focus on the first control inside, not on the page behind", async () => {
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    await screen.findByRole("dialog");
    await waitFor(() => expect(screen.getByLabelText("Pole")).toHaveFocus());
  });

  it("keeps Tab inside while it is modal", async () => {
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    const dialog = await screen.findByRole("dialog");

    // Forward past the last control wraps to the first, rather than landing on
    // "Za oknem" — which is visually behind a scrim and cannot be seen.
    await userEvent.tab();
    await userEvent.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);

    await userEvent.tab({ shift: true });
    await userEvent.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("does not trap Tab when it is not modal", async () => {
    render(<Page modal={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Otwórz" }));
    await screen.findByRole("dialog");

    await userEvent.tab();
    await userEvent.tab();
    await userEvent.tab();
    // Somewhere outside is exactly right here: a non-modal panel that swallowed
    // Tab would be a trap with no way to know it is one.
    expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(false);
  });

  it("gives focus back to whatever opened it", async () => {
    render(<Page />);
    const opener = screen.getByRole("button", { name: "Otwórz" });
    await userEvent.click(opener);
    await screen.findByRole("dialog");

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(opener).toHaveFocus());
  });

  it("activates its buttons with Enter and with Space", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <Confirm
        open
        title="Zapomnieć?"
        confirmLabel="Zapomnij"
        danger
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    const dialog = screen.getByRole("dialog");

    within(dialog).getByRole("button", { name: "Zapomnij" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onConfirm).toHaveBeenCalledTimes(1);

    await userEvent.keyboard(" ");
    expect(onConfirm).toHaveBeenCalledTimes(2);
  });
});

describe("a question about something irreversible", () => {
  it("opens with the cancel button focused, not the destructive one", async () => {
    render(
      <Confirm
        open
        title="Zapomnieć?"
        confirmLabel="Zapomnij"
        danger
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    // Enter out of habit should land on "no". This is the whole reason the
    // cancel button comes first in the markup.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Anuluj" })).toHaveFocus(),
    );
  });

  it("marks the destructive button as destructive", () => {
    render(
      <Confirm
        open
        title="Usunąć klucz?"
        confirmLabel="Usuń klucz"
        danger
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Usuń klucz" })).toHaveClass("btn--danger");
  });

  it("treats Escape as cancelling, never as confirming", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <Confirm
        open
        title="Zapomnieć?"
        confirmLabel="Zapomnij"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});

describe("when the person asked for calm", () => {
  it("still opens, closes and returns focus with movement switched off", async () => {
    document.documentElement.dataset.motion = "off";
    render(<Page />);
    const opener = screen.getByRole("button", { name: "Otwórz" });

    await userEvent.click(opener);
    const dialog = await screen.findByRole("dialog");
    // Reduced motion is not reduced function: the trap still holds.
    await userEvent.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(opener).toHaveFocus();
  });
});
