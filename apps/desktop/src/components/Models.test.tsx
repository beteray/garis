/**
 * The model screen shows measured state and never leaks the key.
 *
 * The failure it replaces: "klucz w sejfie" for a key the provider rejects, and
 * `vault://openai_api_key` printed next to it.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModelsSettings } from "./Models";
import { MemoryView } from "./Memory";
import { engine, fakeApi, given, memory, provider, secret } from "../test/fixtures";

const withProviders = (...providers: ReturnType<typeof provider>[]) =>
  engine({ models: { ...engine().models, providers } });

describe("provider state", () => {
  it("says a rejected key is rejected, not that it is stored", () => {
    given({
      engine: withProviders(
        provider({ status: "invalid_key", reason: "Klucz odrzucony przez dostawcę.", available: false }),
      ),
      secrets: [secret({ name: "gemini_api_key" })],
    });
    render(<ModelsSettings />);

    expect(screen.getByText("Zły klucz")).toBeInTheDocument();
    expect(screen.getByText("Klucz odrzucony przez dostawcę.")).toBeInTheDocument();
    expect(screen.getByText(/Popraw klucz/)).toBeInTheDocument();
  });

  it("separates a limit that will clear from one that will not", () => {
    const { unmount } = render(<Limited retryAfter={300} />);
    expect(screen.getByText(/Spróbuję ponownie za 5 min/)).toBeInTheDocument();
    unmount();

    render(<Limited retryAfter={0} />);
    expect(screen.getByText(/nie minie samo/)).toBeInTheDocument();
  });

  it("says when it has not checked rather than guessing", () => {
    given({
      engine: withProviders(provider({ status: "unknown", reason: "Jeszcze nie sprawdzałem." })),
    });
    render(<ModelsSettings />);
    expect(screen.getByText("Sprawdzam")).toBeInTheDocument();
    // The engine's own sentence. The unconfigured providers beside it say
    // "Brak klucza." — absence of a key and absence of a measurement are not
    // the same thing and do not share a wording.
    expect(screen.getByText("Jeszcze nie sprawdzałem.")).toBeInTheDocument();
    expect(screen.getAllByText("Brak klucza.")).toHaveLength(2);
  });

  it("re-checks on demand", async () => {
    const checkProviders = vi.fn(async () => ({ providers: [provider()] }));
    given({ api: fakeApi({ checkProviders }) });
    render(<ModelsSettings />);

    await userEvent.click(screen.getByRole("button", { name: "Sprawdź ponownie" }));
    await waitFor(() => expect(checkProviders).toHaveBeenCalled());
  });

  it("keeps technical detail out of the ordinary view", () => {
    const detail = "HTTP 400: API key not valid";
    given({
      engine: withProviders(provider({ status: "invalid_key", detail })),
    });
    const { unmount } = render(<ModelsSettings />);
    expect(screen.queryByText(detail)).not.toBeInTheDocument();
    unmount();

    given({
      engine: {
        ...withProviders(provider({ status: "invalid_key", detail })),
        dev: { verbose: false, developer_mode: true },
      },
    });
    render(<ModelsSettings />);
    expect(screen.getByText(detail)).toBeInTheDocument();
  });
});

function Limited({ retryAfter }: { retryAfter: number }) {
  given({
    engine: withProviders(
      provider({
        status: "rate_limit",
        available: false,
        retry_after: retryAfter,
        reason: retryAfter ? "Limit u dostawcy wyczerpany." : "Brak środków na koncie u dostawcy.",
      }),
    ),
  });
  return <ModelsSettings />;
}

describe("the key flow", () => {
  it("goes editing → checking → saved, and says so at each step", async () => {
    let resolveSave: (value: unknown) => void = () => {};
    const storeSecret = vi.fn(
      () => new Promise((resolve) => {
        resolveSave = resolve;
      }),
    );
    given({ api: fakeApi({ storeSecret }) });
    render(<ModelsSettings />);

    const field = screen.getByLabelText("Klucz OpenAI");
    await userEvent.type(field, "sk-test");
    await userEvent.click(screen.getAllByRole("button", { name: "Zapisz i sprawdź" })[0]);

    // The engine health-checks before it replies, so this is a real phase.
    await waitFor(() => expect(screen.getByText("Sprawdzam u dostawcy…")).toBeInTheDocument());
    resolveSave({ ref: "vault://openai_api_key" });
    await waitFor(() => expect(screen.getByText("Zapisane i sprawdzone.")).toBeInTheDocument());
  });

  it("reports a refusal instead of pretending it saved", async () => {
    given({
      api: fakeApi({
        storeSecret: async () => {
          throw new Error("Sekret nie może trafić do pamięci.");
        },
      }),
    });
    render(<ModelsSettings />);

    await userEvent.type(screen.getByLabelText("Klucz OpenAI"), "sk-test");
    await userEvent.click(screen.getAllByRole("button", { name: "Zapisz i sprawdź" })[0]);

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Sekret nie może trafić do pamięci."),
    );
  });

  it("never renders the key, its storage name, or a vault path", async () => {
    given({ secrets: [secret()] });
    const { container } = render(<ModelsSettings />);

    await waitFor(() => expect(screen.getByText("Klucz OpenAI")).toBeInTheDocument());
    expect(container.textContent).not.toContain("vault://");
    expect(container.textContent).not.toContain("openai_api_key");
  });
});

describe("memory and the vault", () => {
  it("keeps them apart and explains why", async () => {
    given({ memories: [memory()], secrets: [secret()] });
    render(<MemoryView />);

    await waitFor(() => expect(screen.getByText("Wolę krótkie odpowiedzi")).toBeInTheDocument());
    expect(screen.getByText(/nie są pamięcią/)).toBeInTheDocument();
  });

  it("names a secret in human words and shows nothing else about it", async () => {
    given({ memories: [], secrets: [secret()] });
    const { container } = render(<MemoryView />);

    await waitFor(() => expect(screen.getByText("Klucz OpenAI")).toBeInTheDocument());
    expect(container.textContent).not.toContain("vault://");
    expect(container.textContent).not.toContain("openai_api_key");
    expect(screen.getByText("ukryte")).toBeInTheDocument();
  });

  it("says what an empty vault means rather than showing a blank panel", async () => {
    given({ memories: [], secrets: [] });
    render(<MemoryView />);
    await waitFor(() => expect(screen.getByText("Sejf jest pusty.")).toBeInTheDocument());
  });

  it("lets a memory be corrected and forgotten, because it is the user's", async () => {
    const forget = vi.fn(async () => ({ forgotten: true }));
    given({ memories: [memory()], api: fakeApi({ forget }) });
    render(<MemoryView />);

    await waitFor(() => expect(screen.getByText("Wolę krótkie odpowiedzi")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Zapomnij" }));

    // Forgetting is asked about first: it cannot be undone, and the button sits
    // next to "Popraw".
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Nie da się tego cofnąć");
    expect(forget).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole("button", { name: "Zapomnij" }));
    await waitFor(() => expect(forget).toHaveBeenCalledWith("m1"));
  });
});
