/**
 * The daily surface says what happened, in words, and never flattens a claim.
 *
 * The failures being guarded: everything looking like the same bubble so the
 * one line that mattered was invisible, and "done" being shown for a task
 * nothing verified.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Home } from "./Home";
import { useStore } from "../lib/store";
import { LONG_POLISH, approval, engine, fakeApi, given, task } from "../test/fixtures";

const entry = (over: Record<string, unknown> = {}) => ({
  id: "e1",
  text: "Cześć.",
  at: Date.now() / 1000,
  kind: "chat" as const,
  ...over,
});

describe("home", () => {
  it("invites the first message instead of showing an empty box", () => {
    given({});
    render(<Home state="idle" />);
    expect(screen.getByText("Jeszcze nic tu nie ma.")).toBeInTheDocument();
    // And says the thing the classifier fix made true.
    expect(screen.getByText(/nie zrobię z tego zadania/)).toBeInTheDocument();
  });

  it("says what GARIS is doing in words, not only in colour", () => {
    given({});
    render(<Home state="executing" />);
    expect(screen.getByText("Wykonuję")).toBeInTheDocument();
  });

  it("labels each kind of entry so they cannot be confused", () => {
    given({
      chat: [
        entry({ id: "1", kind: "user", text: "policz 2+2" }),
        entry({ id: "2", kind: "answer", text: "4" }),
        entry({ id: "3", kind: "verified", text: "Zrobione." }),
        entry({ id: "4", kind: "unverified", text: "Zrobione." }),
        entry({ id: "5", kind: "failure", text: "Nie udało się." }),
      ],
    });
    render(<Home state="idle" />);

    expect(screen.getByText("Odpowiedź")).toBeInTheDocument();
    // The two "Zrobione." entries carry different claims and say so.
    expect(screen.getByText("Zrobione i sprawdzone")).toBeInTheDocument();
    expect(screen.getByText("Zrobione, niesprawdzone")).toBeInTheDocument();
    expect(screen.getByText("Nie udało się")).toBeInTheDocument();
  });

  it("pins what needs a decision above the thread", () => {
    given({
      tasks: [task({ state: "blocked", error: "Który dysk mam sprawdzić?" })],
      chat: [entry()],
    });
    render(<Home state="waiting_input" />);

    const pinned = screen.getByLabelText("Wymaga Twojej decyzji");
    expect(pinned).toHaveTextContent("Który dysk mam sprawdzić?");
  });

  it("never says only that it is waiting", () => {
    given({ tasks: [task({ state: "blocked", error: "" })] });
    render(<Home state="waiting_input" />);
    // A blocked task with no question is an engine bug, and is named as one.
    expect(screen.getByText(/błąd silnika/)).toBeInTheDocument();
  });

  it("shows an approval as a decision with its effects", () => {
    given({
      tasks: [task({ state: "blocked" })],
      approvals: [approval()],
    });
    render(<Home state="waiting_approval" />);

    expect(screen.getByText(/Zainstalować 7-Zip/)).toBeInTheDocument();
    expect(screen.getByText("install")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zgoda" })).toBeInTheDocument();
  });

  it("resolves an approval through the engine, not locally", async () => {
    const resolveApproval = vi.fn(async () => approval({ state: "approved" }));
    given({
      tasks: [task({ state: "blocked" })],
      approvals: [approval()],
      api: fakeApi({ resolveApproval }),
    });
    render(<Home state="waiting_approval" />);

    await userEvent.click(screen.getByRole("button", { name: "Zgoda" }));
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith("a1", true));
  });

  it("wraps a long Polish request instead of widening the panel", () => {
    given({ chat: [entry({ text: LONG_POLISH, kind: "user" })] });
    render(<Home state="idle" />);
    const message = screen.getByText(LONG_POLISH);
    expect(message).toHaveClass("message__text");
    // The rule that does the work lives on .message; assert it is the element
    // carrying the text, so a refactor that drops the class fails here.
    expect(message.closest(".message")).not.toBeNull();
  });

  it("does not render three hundred entries at once", () => {
    given({
      chat: Array.from({ length: 120 }, (_, index) =>
        entry({ id: `e${index}`, text: `wiadomość ${index}` }),
      ),
    });
    render(<Home state="idle" />);

    expect(screen.queryByText("wiadomość 0")).not.toBeInTheDocument();
    expect(screen.getByText("wiadomość 119")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pokaż wcześniejsze/ })).toBeInTheDocument();
  });
});

describe("composer", () => {
  it("sends what was typed through /api/say, never straight to a task", async () => {
    const say = vi.fn(async () => ({ kind: "chat", text: "Cześć.", intent: "greeting" }));
    const submit = vi.fn();
    given({ api: fakeApi({ say, submit }) });
    render(<Home state="idle" />);

    await userEvent.type(screen.getByLabelText(/Powiedz, co ma być zrobione/), "cześć");
    await userEvent.click(screen.getByRole("button", { name: "Wyślij" }));

    await waitFor(() => expect(say).toHaveBeenCalledWith("cześć"));
    expect(submit).not.toHaveBeenCalled();
  });

  it("has one explicit path that does create a task", async () => {
    const say = vi.fn();
    const submit = vi.fn(async () => ({ task_id: "t9" }));
    given({ api: fakeApi({ say, submit }) });
    render(<Home state="idle" />);

    await userEvent.type(screen.getByLabelText(/Powiedz, co ma być zrobione/), "zrób to");
    await userEvent.click(screen.getByRole("button", { name: "Zadanie" }));

    await waitFor(() => expect(submit).toHaveBeenCalledWith("zrób to"));
    expect(say).not.toHaveBeenCalled();
  });

  it("puts an answer in the thread without creating a task", async () => {
    given({
      api: fakeApi({
        say: async () => ({ kind: "chat", text: "4", intent: "arithmetic" }),
      }),
    });
    render(<Home state="idle" />);

    await userEvent.type(screen.getByLabelText(/Powiedz, co ma być zrobione/), "2+2");
    await userEvent.click(screen.getByRole("button", { name: "Wyślij" }));

    await waitFor(() => expect(screen.getByText("4")).toBeInTheDocument());
    expect(useStore.getState().tasks).toHaveLength(0);
  });

  it("does not open a microphone it has no pipeline for", () => {
    given({});
    render(<Home state="idle" />);
    expect(screen.getByLabelText(/Mówienie/)).toBeDisabled();
  });

  it("stays usable while a request is open", async () => {
    given({ sending: true, engine: engine() });
    render(<Home state="receiving" />);
    expect(screen.getByRole("button", { name: "Wysyłam…" })).toBeDisabled();
    expect(screen.getByText("Przyjmuję")).toBeInTheDocument();
  });
});
