import { useCallback, useEffect, useState } from "react";
import { AgentEditor } from "./AgentEditor";
import { RunDetail } from "./RunDetail";
import {
  api,
  formatCost,
  formatLatency,
  inputLabel,
  type Agent,
  type Client,
  type InputSource,
  type Providers,
  type Run,
  type RunSummary,
} from "./api";

function errorText(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

export default function App() {
  const [providers, setProviders] = useState<Providers>({});
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState<number | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [inputs, setInputs] = useState<InputSource[]>([]);
  const [agentId, setAgentId] = useState<number | null>(null);
  const [editing, setEditing] = useState<"new" | number | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [newClient, setNewClient] = useState({ name: "", contact_email: "" });
  const [pasted, setPasted] = useState("");
  const [inputId, setInputId] = useState<number | null>(null);
  const [running, setRunning] = useState(false);

  const agent = agents.find((a) => a.id === agentId) ?? null;
  const client = clients.find((c) => c.id === clientId) ?? null;
  const matchingInputs = inputs.filter((i) => i.kind === agent?.input_kind);

  useEffect(() => {
    Promise.all([api.providers(), api.listClients()])
      .then(([p, c]) => {
        setProviders(p);
        setClients(c);
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  const loadClient = useCallback(async (id: number) => {
    const [a, i] = await Promise.all([api.listAgents(id), api.listInputs(id)]);
    setAgents(a);
    setInputs(i);
  }, []);

  useEffect(() => {
    setAgentId(null);
    setRun(null);
    setEditing(null);
    if (clientId !== null) loadClient(clientId).catch((e) => setError(errorText(e)));
  }, [clientId, loadClient]);

  useEffect(() => {
    setRuns([]);
    setInputId(null);
    if (agentId !== null) api.listRuns(agentId).then(setRuns).catch((e) => setError(errorText(e)));
  }, [agentId]);

  async function guarded(fn: () => Promise<void>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    }
  }

  return (
    <div className="layout">
      <aside>
        <h1>ConsultFlow</h1>
        <h2>Clients</h2>
        <ul className="list">
          {clients.map((c) => (
            <li key={c.id}>
              <button className={c.id === clientId ? "selected" : ""} onClick={() => setClientId(c.id)}>
                {c.name}
              </button>
            </li>
          ))}
        </ul>
        <form
          aria-label="new client"
          onSubmit={(e) => {
            e.preventDefault();
            guarded(async () => {
              const created = await api.createClient({
                name: newClient.name,
                contact_email: newClient.contact_email || null,
              });
              setClients((cs) => [...cs, created].sort((a, b) => a.name.localeCompare(b.name)));
              setNewClient({ name: "", contact_email: "" });
              setClientId(created.id);
            });
          }}
        >
          <input
            placeholder="Client name"
            value={newClient.name}
            onChange={(e) => setNewClient({ ...newClient, name: e.target.value })}
            required
          />
          <input
            placeholder="Contact email (optional)"
            value={newClient.contact_email}
            onChange={(e) => setNewClient({ ...newClient, contact_email: e.target.value })}
          />
          <button type="submit">Create client</button>
        </form>
      </aside>

      <main>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {!client && <p>Select or create a client.</p>}
        {client && (
          <>
            <h2>{client.name}</h2>
            <section>
              <h3>Agents</h3>
              <ul className="list horizontal">
                {agents.map((a) => (
                  <li key={a.id}>
                    <button className={a.id === agentId ? "selected" : ""} onClick={() => setAgentId(a.id)}>
                      {a.name} <small>({a.input_kind})</small>
                    </button>
                  </li>
                ))}
                <li>
                  <button onClick={() => setEditing("new")}>New agent</button>
                </li>
              </ul>
              {editing === "new" && (
                <AgentEditor
                  providers={providers}
                  submitLabel="Save agent"
                  onSubmit={async (cfg) => {
                    const created = await api.createAgent(client.id, cfg);
                    setAgents((as) => [...as, created]);
                    setEditing(null);
                    setAgentId(created.id);
                  }}
                />
              )}
              {agent && editing === agent.id && (
                <AgentEditor
                  key={agent.id}
                  initial={agent}
                  providers={providers}
                  submitLabel="Update agent"
                  onSubmit={async (cfg) => {
                    const updated = await api.updateAgent(agent.id, cfg);
                    setAgents((as) => as.map((a) => (a.id === updated.id ? updated : a)));
                    setEditing(null);
                  }}
                />
              )}
            </section>

            <section className="card">
              <h3>Inputs</h3>
              <label>
                Upload document or .eml
                <input
                  type="file"
                  aria-label="upload input"
                  accept=".txt,.md,.csv,.json,.log,.pdf,.html,.htm,.eml"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    guarded(async () => {
                      const src = await api.uploadInput(client.id, file);
                      setInputs((xs) => [...xs, src]);
                      e.target.value = "";
                    });
                  }}
                />
              </label>
              <label>
                Paste email (headers optional)
                <textarea
                  aria-label="pasted email"
                  rows={5}
                  value={pasted}
                  onChange={(e) => setPasted(e.target.value)}
                />
              </label>
              <button
                disabled={!pasted.trim()}
                onClick={() =>
                  guarded(async () => {
                    const src = await api.pasteEmail(client.id, pasted);
                    setInputs((xs) => [...xs, src]);
                    setPasted("");
                  })
                }
              >
                Save email
              </button>
              <ul className="list">
                {inputs.map((i) => (
                  <li key={i.id}>{inputLabel(i)}</li>
                ))}
              </ul>
            </section>

            {agent && (
              <section className="card" aria-label="run agent">
                <h3>
                  Run “{agent.name}”{" "}
                  <button onClick={() => setEditing(agent.id)}>Edit configuration</button>
                </h3>
                <p>
                  {agent.provider}/{agent.model} · {agent.steps.length} step(s) · input: {agent.input_kind}
                </p>
                <label>
                  Input
                  <select
                    aria-label="run input"
                    value={inputId ?? ""}
                    onChange={(e) => setInputId(e.target.value ? Number(e.target.value) : null)}
                  >
                    <option value="">Choose a {agent.input_kind}</option>
                    {matchingInputs.map((i) => (
                      <option key={i.id} value={i.id}>
                        {inputLabel(i)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  disabled={inputId === null || running}
                  onClick={() =>
                    guarded(async () => {
                      setRunning(true);
                      try {
                        const result = await api.startRun(agent.id, inputId!);
                        setRun(result);
                        setRuns(await api.listRuns(agent.id));
                      } finally {
                        setRunning(false);
                      }
                    })
                  }
                >
                  {running ? "Running…" : "Run agent"}
                </button>
                <h4>Run history</h4>
                <table>
                  <thead>
                    <tr>
                      <th>Run</th>
                      <th>Status</th>
                      <th>Input</th>
                      <th>Cost</th>
                      <th>Latency</th>
                      <th>Started</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.id}>
                        <td>
                          <button onClick={() => guarded(async () => setRun(await api.getRun(r.id)))}>#{r.id}</button>
                        </td>
                        <td>{r.status}</td>
                        <td>#{r.input_id}</td>
                        <td>{formatCost(r.total_cost_usd)}</td>
                        <td>{formatLatency(r.total_latency_ms)}</td>
                        <td>{new Date(r.started_at).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
            {run && <RunDetail run={run} />}
          </>
        )}
      </main>
    </div>
  );
}
