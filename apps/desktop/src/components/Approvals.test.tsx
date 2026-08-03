/**
 * A request for permission must be impossible to miss and impossible to answer
 * twice. Those two pull in opposite directions, and this is where the balance
 * is written down.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ApprovalLayer } from "./Approvals";
import { approval, given, task } from "../test/fixtures";

describe("where a request for permission appears", () => {
  it("follows the user onto a screen that cannot show it", () => {
    given({
      view: "settings",
      tasks: [task({ state: "blocked" })],
      approvals: [approval()],
    });
    render(<ApprovalLayer />);
    expect(screen.getByText(/Zainstalować 7-Zip/)).toBeInTheDocument();
  });

  it("stays quiet where the screen is already asking", () => {
    // Home pins blocked tasks above the thread, so the same question here would
    // be the same question twice, ten pixels apart.
    given({
      view: "home",
      tasks: [task({ state: "blocked" })],
      approvals: [approval()],
    });
    render(<ApprovalLayer />);
    expect(screen.queryByText(/Zainstalować 7-Zip/)).toBeNull();
  });

  it("appears anyway for a request Home pins nowhere", () => {
    // Home pins three. The fourth has no card of its own, and an unanswerable
    // request is worse than a duplicated one.
    const blocked = ["t1", "t2", "t3", "t4"].map((id) => task({ id, state: "blocked" }));
    given({
      view: "home",
      tasks: blocked,
      approvals: [approval({ id: "a4", task_id: "t4" })],
    });
    render(<ApprovalLayer />);
    expect(screen.getByText(/Zainstalować 7-Zip/)).toBeInTheDocument();
  });

  it("shows a request that belongs to no task at all", () => {
    given({ view: "home", tasks: [], approvals: [approval({ task_id: "" })] });
    render(<ApprovalLayer />);
    expect(screen.getByText(/Zainstalować 7-Zip/)).toBeInTheDocument();
  });
});
