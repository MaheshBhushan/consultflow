import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Agent, AgentConfig, Client, InputSource, Run } from "./api";

/** Minimal in-memory stand-in for the FastAPI backend, routed through global fetch. */
interface FakeBackend {
  handler: (url: string, init?: RequestInit) => Promise<Response>;
  requests: { method: string; path: string; body: unknown }[];
}

function fakeBackend(): FakeBackend {
  const clients: Client[] = [];
  const agents: Agent[] = [];
  const inputs: InputSource[] = [];
  const runs: Run[] = [];
  const requests: { method: string; path: string; body: unknown }[] = [];
  const now = "2026-11-11T10:00:00Z";
  const ok = (data: unknown, status = 200) =>
    new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });

  const handler = async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const path = url.replace(/^\/api/, "");
    const body: any = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
    requests.push({ method, path, body });
    let m: RegExpMatchArray | null;
    if (path === "/providers")
      return ok({ offline: { models: ["offline-extractive-v1"], available: true }, openai: { models: ["gpt-4o-mini"], available: false } });
    if (path === "/clients" && method === "GET") return ok(clients);
    if (path === "/clients" && method === "POST") {
      const c = { id: clients.length + 1, notes: "", created_at: now, ...body } as Client;
      clients.push(c);
      return ok(c, 201);
    }
    if ((m = path.match(/^\/clients\/(\d+)\/agents$/))) {
      if (method === "GET") return ok(agents.filter((a) => a.client_id === Number(m![1])));
      const cfg: AgentConfig = body;
      if (cfg.steps.some((s) => s.prompt.includes("{{bogus}}")))
        return ok({ detail: [{ msg: "Value error, step 'x': unknown variable {{bogus}}" }] }, 422);
      const a: Agent = { ...cfg, id: agents.length + 1, client_id: Number(m[1]), created_at: now, updated_at: now };
      agents.push(a);
      return ok(a, 201);
    }
    if ((m = path.match(/^\/clients\/(\d+)\/inputs$/))) return ok(inputs.filter((i) => i.client_id === Number(m![1])));
    if ((m = path.match(/^\/clients\/(\d+)\/inputs\/email$/))) {
      const src: InputSource = {
        id: inputs.length + 1,
        client_id: Number(m[1]),
        kind: "email",
        origin: "paste",
        filename: null,
        content_type: "message/rfc822",
        sha256: "ab".repeat(32),
        meta: { subject: "Invoice question" },
        text: body.raw,
        created_at: now,
      };
      inputs.push(src);
      return ok(src, 201);
    }
    if ((m = path.match(/^\/agents\/(\d+)\/runs$/))) {
      const agentId = Number(m[1]);
      if (method === "GET") return ok(runs.filter((r) => r.agent_id === agentId));
      const agent = agents.find((a) => a.id === agentId)!;
      const run: Run = {
        id: runs.length + 1,
        agent_id: agentId,
        input_id: body.input_id,
        status: "succeeded",
        error: null,
        total_cost_usd: "0.002000",
        total_latency_ms: 1530,
        total_input_tokens: 1000,
        total_output_tokens: 200,
        started_at: now,
        finished_at: now,
        agent_snapshot: agent,
        final_output: "Key points: invoice total $15,250.00",
        steps: agent.steps.map((s, i) => ({
          id: i + 1,
          position: i + 1,
          name: s.name,
          provider: agent.provider,
          model: agent.model,
          sources: [i === 0 ? "email:#1 Invoice question sha256:abababababab" : `step:${i} ${agent.steps[i - 1].name}`],
          prompt: s.prompt,
          output: `output ${i + 1}`,
          status: "succeeded",
          error: null,
          input_tokens: 500,
          output_tokens: 100,
          cost_usd: "0.001000",
          latency_ms: 765,
          started_at: now,
        })),
      };
      runs.push(run);
      return ok(run, 201);
    }
    if ((m = path.match(/^\/runs\/(\d+)$/))) return ok(runs.find((r) => r.id === Number(m![1])));
    return ok({ detail: `unhandled ${method} ${path}` }, 404);
  };
  return { handler, requests };
}

describe("ConsultFlow workbench", () => {
  let backend: FakeBackend;

  beforeEach(() => {
    backend = fakeBackend();
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => backend.handler(url, init)));
  });

  it("creates a client, configures a two-step agent, runs it on a pasted email and shows the step log", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByPlaceholderText("Client name"), "Northwind Logistics");
    await user.click(screen.getByRole("button", { name: "Create client" }));
    expect(await screen.findByRole("heading", { name: "Northwind Logistics" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New agent" }));
    const editor = screen.getByRole("form", { name: "agent editor" });
    await user.type(within(editor).getByLabelText("Agent name"), "Dispute triage");
    await user.click(within(editor).getByRole("button", { name: "Add step" }));
    const prompt2 = within(editor).getByLabelText("step 2 prompt");
    await user.clear(prompt2);
    await user.type(prompt2, "Draft a reply to {{{{sender}}: {{{{previous}}");
    await user.click(within(editor).getByRole("button", { name: "Save agent" }));

    const created = backend.requests.find((r) => r.method === "POST" && r.path === "/clients/1/agents");
    expect(created?.body).toMatchObject({
      name: "Dispute triage",
      provider: "offline",
      input_kind: "email",
      steps: [{ name: "extract" }, { name: "step2", prompt: "Draft a reply to {{sender}}: {{previous}}" }],
    });

    await user.type(screen.getByLabelText("pasted email"), "Subject: Invoice question{enter}{enter}Please fix line 4.");
    await user.click(screen.getByRole("button", { name: "Save email" }));
    await user.selectOptions(await screen.findByLabelText("run input"), "1");
    await user.click(screen.getByRole("button", { name: "Run agent" }));

    const runSection = await screen.findByRole("region", { name: "run 1" });
    const rows = within(runSection).getAllByRole("row");
    expect(rows).toHaveLength(3); // header + 2 steps
    expect(within(rows[1]).getByText("email:#1 Invoice question sha256:abababababab")).toBeInTheDocument();
    expect(within(rows[2]).getByText("step:1 extract")).toBeInTheDocument();
    expect(within(rows[1]).getByText("$0.001000")).toBeInTheDocument();
    expect(within(rows[1]).getByText("765 ms")).toBeInTheDocument();
    expect(within(runSection).getByText(/total latency 1.53 s/)).toBeInTheDocument();
    expect(screen.getByTestId("final-output")).toHaveTextContent("$15,250.00");

    const history = within(screen.getByRole("region", { name: "run agent" }));
    await waitFor(() => expect(history.getByRole("button", { name: "#1" })).toBeInTheDocument());
  });

  it("shows backend validation errors for an invalid agent", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByPlaceholderText("Client name"), "Acme");
    await user.click(screen.getByRole("button", { name: "Create client" }));
    await user.click(await screen.findByRole("button", { name: "New agent" }));
    const editor = screen.getByRole("form", { name: "agent editor" });
    await user.type(within(editor).getByLabelText("Agent name"), "bad");
    const prompt = within(editor).getByLabelText("step 1 prompt");
    await user.clear(prompt);
    await user.type(prompt, "{{{{bogus}}");
    await user.click(within(editor).getByRole("button", { name: "Save agent" }));
    expect(await within(editor).findByText(/unknown variable \{\{bogus\}\}/)).toBeInTheDocument();
  });

  it("marks providers without an API key", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByPlaceholderText("Client name"), "Acme");
    await user.click(screen.getByRole("button", { name: "Create client" }));
    await user.click(await screen.findByRole("button", { name: "New agent" }));
    expect(screen.getByRole("option", { name: "openai (no API key)" })).toBeInTheDocument();
  });
});
