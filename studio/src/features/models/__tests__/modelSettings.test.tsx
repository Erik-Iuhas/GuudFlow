import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ModelSettings } from "../ModelSettings";
import { envNameFor, fromPerMillion, perMillion, tierModels, toDoc } from "../model";
import type { ModelConfigView } from "../api";

const VIEW: ModelConfigView = {
  custom: false,
  providers: [
    { name: "anthropic", client: "anthropic", base_url: "", api_key_env: "ANTHROPIC_API_KEY", key: "env" },
    { name: "acme", client: "openai_compatible", base_url: "https://llm.acme.test/v1", api_key_env: "ACME_API_KEY", key: "missing" },
  ],
  models: [
    { id: "claude-opus-5-5", provider: "anthropic", tier: "heavy", cost_per_1k_input: 0.004, cost_per_1k_output: 0.02, cache_read_per_1k: 0.0002 },
    { id: "claude-sonnet-5-5", provider: "anthropic", tier: "solid", cost_per_1k_input: 0.002, cost_per_1k_output: 0.01 },
    { id: "acme-small", provider: "acme", tier: "fast", cost_per_1k_input: 0.0001, cost_per_1k_output: 0.0002 },
  ],
  tiers: {},
};

describe("model helpers", () => {
  it("converts prices between per 1k and per million", () => {
    expect(perMillion(0.004)).toBe("4");
    expect(perMillion(0.0002)).toBe("0.2");
    expect(fromPerMillion("4")).toBeCloseTo(0.004);
    expect(fromPerMillion("")).toBeUndefined();
    expect(fromPerMillion("cheap")).toBeNull();
    expect(fromPerMillion("-1")).toBeNull();
  });

  it("names env vars, picks tier models and builds the document without key status", () => {
    expect(envNameFor("my-llm")).toBe("MY_LLM_API_KEY");
    expect(tierModels(VIEW.models, {}, "solid")).toEqual(["claude-sonnet-5-5"]);
    expect(tierModels(VIEW.models, { solid: ["claude-opus-5-5", "gone", "acme-small"] }, "solid"))
      .toEqual(["claude-opus-5-5", "acme-small"]);
    const doc = toDoc(VIEW);
    expect(doc.providers.anthropic).toEqual({ client: "anthropic", api_key_env: "ANTHROPIC_API_KEY" });
    expect(JSON.stringify(doc)).not.toContain("\"key\"");
  });
});

describe("ModelSettings", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let view: ModelConfigView;
  const calls = () => fetchMock.mock.calls.map(([url, init]) => ({
    url: String(url), method: (init as RequestInit | undefined)?.method ?? "GET",
    body: (init as RequestInit | undefined)?.body ? JSON.parse(String((init as RequestInit).body)) : undefined,
  }));

  beforeEach(() => {
    view = structuredClone(VIEW);
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "PUT" && url === "/api/model-config") {
        const doc = JSON.parse(String(init?.body));
        if ((doc.tiers.heavy ?? []).includes("bad")) return new Response(JSON.stringify({ detail: "tier heavy: unknown model 'bad'" }), { status: 422 });
        view = { ...view, custom: true, models: doc.models, tiers: doc.tiers };
      }
      if (method === "PUT" && url.endsWith("/key")) {
        view = { ...view, providers: view.providers.map((p) => (url.includes("/acme/") ? { ...p, key: p.name === "acme" ? "stored" : p.key } : p)) };
      }
      if (url.endsWith("/test")) {
        return new Response(JSON.stringify({ ok: true, detail: "2 model(s) available", models: ["a", "b"] }));
      }
      return new Response(JSON.stringify(view));
    });
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  const renderPage = () => render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ModelSettings />
    </QueryClientProvider>,
  );

  it("shows where each key comes from, never a key", async () => {
    renderPage();
    const anthropic = await screen.findByTestId("provider-anthropic");
    expect(anthropic).toHaveTextContent("key from $ANTHROPIC_API_KEY");
    expect(screen.getByTestId("provider-acme")).toHaveTextContent("no key");
    expect(screen.getByText("shipped defaults")).toBeInTheDocument();
  });

  it("saves a key through a password field and clears it", async () => {
    renderPage();
    const acme = await screen.findByTestId("provider-acme");
    fireEvent.click(within(acme).getByRole("button", { name: "Set key" }));
    const input = within(acme).getByLabelText("API key for acme");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.change(input, { target: { value: "sk-acme-123" } });
    fireEvent.click(within(acme).getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(screen.getByTestId("provider-acme")).toHaveTextContent("key saved on this machine"));
    const put = calls().find((c) => c.method === "PUT" && c.url.endsWith("/acme/key"));
    expect(put?.body).toEqual({ key: "sk-acme-123" });
    expect(screen.queryByDisplayValue("sk-acme-123")).toBeNull();
  });

  it("tests a provider and shows what it answered", async () => {
    renderPage();
    const acme = await screen.findByTestId("provider-acme");
    fireEvent.click(within(acme).getByRole("button", { name: "Test" }));
    expect(await within(acme).findByText(/2 model\(s\) available/)).toBeInTheDocument();
  });

  it("edits a price per million and a tier, then saves per 1k", async () => {
    renderPage();
    const price = await screen.findByLabelText("Input price for claude-sonnet-5-5");
    fireEvent.change(price, { target: { value: "3" } });
    fireEvent.blur(price);
    // Several models per tier: add one, then the first can be removed.
    fireEvent.change(screen.getByLabelText("Add a model to solid"), { target: { value: "claude-opus-5-5" } });
    expect(within(screen.getByTestId("tier-solid")).getByText(/claude-sonnet-5-5 · default/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Add a model to solid"), { target: { value: "acme-small" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove claude-sonnet-5-5 from solid" }));
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    // Keys can't change while the draft is unsaved (the key routes would save a stale view).
    expect(within(screen.getByTestId("provider-acme")).getByRole("button", { name: "Set key" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Save models & prices" }));
    await screen.findByText("Saved. New runs use these settings.");
    const put = calls().find((c) => c.method === "PUT" && c.url === "/api/model-config");
    const sonnet = put?.body.models.find((m: { id: string }) => m.id === "claude-sonnet-5-5");
    expect(sonnet.cost_per_1k_input).toBeCloseTo(0.003);
    expect(put?.body.tiers.solid).toEqual(["claude-opus-5-5", "acme-small"]);
    expect(screen.getByText("custom")).toBeInTheDocument();
  });

  it("shows the server's reason when a save is refused", async () => {
    renderPage();
    await screen.findByTestId("provider-acme");
    // Force an invalid tier choice through the add-model select.
    const heavy = screen.getByLabelText("Add a model to heavy") as HTMLSelectElement;
    const bad = document.createElement("option");
    bad.value = "bad";
    heavy.appendChild(bad);
    fireEvent.change(heavy, { target: { value: "bad" } });
    fireEvent.click(screen.getByRole("button", { name: "Save models & prices" }));
    expect(await screen.findByText("tier heavy: unknown model 'bad'")).toBeInTheDocument();
  });

  it("adds a provider and a model to the draft", async () => {
    renderPage();
    await screen.findByTestId("provider-acme");
    fireEvent.click(screen.getByText("Add a provider"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "together" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.together.xyz/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "Add provider" }));
    expect(screen.getByText(/save to add its key/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Add a model"), { target: { value: "llama-4" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByTestId("model-llama-4")).toBeInTheDocument();
  });

  it("removing a model drops it from the tiers it was in", async () => {
    renderPage();
    await screen.findByTestId("provider-acme");
    fireEvent.change(screen.getByLabelText("Add a model to fast"), { target: { value: "claude-opus-5-5" } });
    expect(within(screen.getByTestId("tier-fast")).getByText(/claude-opus-5-5/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Remove claude-opus-5-5" }));
    expect(within(screen.getByTestId("tier-fast")).queryByText(/claude-opus-5-5/)).toBeNull();
  });
});
