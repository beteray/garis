/**
 * A task card tells the truth about what happened and what is missing.
 *
 * The claims being policed: never only "czekam na Ciebie"; never a percentage;
 * and "done" never standing in for "done and checked".
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TaskCard } from "./TaskCard";
import { TasksView } from "./Tasks";
import { LONG_POLISH, approval, engine, fakeApi, given, task } from "../test/fixtures";

describe("a task in flight", () => {
  it("uses the goal in the user's own words as the title", () => {
    given({ tasks: [task({ goal: LONG_POLISH })] });
    render(<TaskCard taskId="t1" />);
    expect(screen.getByText(LONG_POLISH)).toBeInTheDocument();
  });

  it("names the state in a word", () => {
    given({ tasks: [task({ state: "running" })] });
    render(<TaskCard taskId="t1" />);
    expect(screen.getByText("Robię")).toBeInTheDocument();
  });

  it("can be stopped", async () => {
    const stopTask = vi.fn(async () => ({ stopped: true }));
    given({ tasks: [task({ state: "running" })], api: fakeApi({ stopTask }) });
    render(<TaskCard taskId="t1" />);

    await userEvent.click(screen.getByRole("button", { name: "Zatrzymaj" }));
    await waitFor(() => expect(stopTask).toHaveBeenCalledWith("t1"));
  });

  it("counts steps and never invents a percentage", async () => {
    given({
      tasks: [task({ state: "running" })],
      api: fakeApi({
        task: async () =>
          task({
            steps: [
              { task_id: "t1", step_key: "a", ordinal: 0, tool: "disk_usage", state: "done", attempts: 1 },
              { task_id: "t1", step_key: "b", ordinal: 1, tool: "list_dir", state: "running", attempts: 1 },
              { task_id: "t1", step_key: "c", ordinal: 2, tool: "report", state: "pending", attempts: 0 },
            ],
          }),
      }),
    });
    render(<TaskCard taskId="t1" />);

    await userEvent.click(screen.getByRole("button", { name: "Szczegóły" }));
    await waitFor(() => expect(screen.getByText(/1 z 3 zrobionych/)).toBeInTheDocument());
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    expect(screen.getByText("list_dir")).toBeInTheDocument();
  });
});

describe("a task that is waiting", () => {
  it("shows the actual question", () => {
    given({ tasks: [task({ state: "blocked", error: "Który dysk mam sprawdzić — C czy D?" })] });
    render(<TaskCard taskId="t1" />);
    expect(screen.getByText("Który dysk mam sprawdzić — C czy D?")).toBeInTheDocument();
  });

  it("shows the actual operation when it is an approval", () => {
    given({ tasks: [task({ state: "blocked" })], approvals: [approval()] });
    render(<TaskCard taskId="t1" />);

    expect(screen.getByText("Czekam na zgodę")).toBeInTheDocument();
    expect(screen.getByText(/Zainstalować 7-Zip/)).toBeInTheDocument();
  });

  it("calls a missing question what it is — a bug, not a state", () => {
    given({ tasks: [task({ state: "blocked", error: "" })] });
    render(<TaskCard taskId="t1" />);
    expect(screen.getByText(/błąd silnika/)).toBeInTheDocument();
  });
});

describe("a task that finished", () => {
  it("distinguishes verified from merely finished", () => {
    given({
      tasks: [
        task({ id: "t1", state: "finished", report: { short: "Gotowe.", verified: true } }),
      ],
    });
    const { unmount } = render(<TaskCard taskId="t1" />);
    expect(screen.getByText("Zrobione i sprawdzone")).toBeInTheDocument();
    unmount();

    given({
      tasks: [
        task({ id: "t1", state: "finished", report: { short: "Gotowe.", verified: false } }),
      ],
    });
    render(<TaskCard taskId="t1" />);
    expect(screen.getByText("Zrobione, niesprawdzone")).toBeInTheDocument();
    expect(screen.getByText(/nie potwierdziłem wyniku/)).toBeInTheDocument();
  });

  it("says what is missing when only part of it worked", () => {
    given({
      tasks: [
        task({
          state: "finished",
          report: { short: "Przeniosłem 8 z 12 plików.", verified: true, problems: ["4 pliki są zablokowane"] },
        }),
      ],
    });
    render(<TaskCard taskId="t1" />);

    expect(screen.getByText("Zrobione częściowo")).toBeInTheDocument();
    expect(screen.getByText(/4 pliki są zablokowane/)).toBeInTheDocument();
  });

  it("offers a retry after a failure", async () => {
    const submit = vi.fn(async () => ({ task_id: "t2" }));
    given({
      tasks: [task({ state: "failed", error: "Brak dostępu do katalogu." })],
      api: fakeApi({ submit }),
    });
    render(<TaskCard taskId="t1" />);

    expect(screen.getByText("Brak dostępu do katalogu.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Spróbuj jeszcze raz" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(task().goal));
  });

  it("hides the task id unless developer mode is on", async () => {
    given({ tasks: [task()] });
    const { unmount } = render(<TaskCard taskId="t1" defaultOpen />);
    expect(screen.queryByText("t1")).not.toBeInTheDocument();
    unmount();

    given({
      tasks: [task()],
      engine: engine({ dev: { verbose: false, developer_mode: true } }),
    });
    render(<TaskCard taskId="t1" defaultOpen />);
    expect(screen.getByText("t1")).toBeInTheDocument();
  });
});

describe("the tasks screen", () => {
  it("says so when nothing has been asked for", () => {
    given({});
    render(<TasksView />);
    expect(screen.getByText("Jeszcze nic nie robiłem.")).toBeInTheDocument();
  });

  it("puts what needs a person first", () => {
    given({
      tasks: [
        task({ id: "done", state: "finished", goal: "skończone" }),
        task({ id: "blocked", state: "blocked", goal: "czeka", error: "Który plik?" }),
        task({ id: "running", state: "running", goal: "trwa" }),
      ],
    });
    render(<TasksView />);

    const goals = screen.getAllByText(/skończone|czeka|trwa/);
    expect(goals.map((node) => node.textContent)).toEqual(["czeka", "trwa", "skończone"]);
  });

  it("filters to the group that matters", async () => {
    given({
      tasks: [
        task({ id: "a", state: "finished", goal: "skończone" }),
        task({ id: "b", state: "blocked", goal: "czeka", error: "Który plik?" }),
      ],
    });
    render(<TasksView />);

    await userEvent.click(screen.getByRole("tab", { name: /Wymaga Ciebie/ }));
    expect(screen.getByText("czeka")).toBeInTheDocument();
    expect(screen.queryByText("skończone")).not.toBeInTheDocument();
  });
});
