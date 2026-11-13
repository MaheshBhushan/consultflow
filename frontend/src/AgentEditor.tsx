import { useState } from "react";
import type { AgentConfig, InputKind, Providers, Step } from "./api";

export const DEFAULT_AGENT: AgentConfig = {
  name: "",
  provider: "offline",
  model: "offline-extractive-v1",
  system_prompt: "You assist consultants working for {{client}}.",
  input_kind: "email",
  steps: [{ name: "extract", prompt: "Extract the key facts, amounts and dates:\n{{input}}" }],
};

interface Props {
  initial?: AgentConfig;
  providers: Providers;
  submitLabel: string;
  onSubmit: (cfg: AgentConfig) => Promise<void>;
}

export function AgentEditor({ initial = DEFAULT_AGENT, providers, submitLabel, onSubmit }: Props) {
  const [cfg, setCfg] = useState<AgentConfig>(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = <K extends keyof AgentConfig>(key: K, value: AgentConfig[K]) => setCfg((c) => ({ ...c, [key]: value }));
  const setStep = (i: number, patch: Partial<Step>) =>
    set(
      "steps",
      cfg.steps.map((s, j) => (j === i ? { ...s, ...patch } : s)),
    );
  const providerNames = Object.keys(providers);
  const models = providers[cfg.provider]?.models ?? [];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSubmit(cfg);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit} aria-label="agent editor">
      <label>
        Agent name
        <input value={cfg.name} onChange={(e) => set("name", e.target.value)} required />
      </label>
      <div className="row">
        <label>
          Provider
          <select
            value={cfg.provider}
            onChange={(e) => {
              const p = e.target.value;
              setCfg((c) => ({ ...c, provider: p, model: providers[p]?.models[0] ?? "" }));
            }}
          >
            {providerNames.map((p) => (
              <option key={p} value={p}>
                {p}
                {providers[p].available ? "" : " (no API key)"}
              </option>
            ))}
          </select>
        </label>
        <label>
          Model
          <select value={cfg.model} onChange={(e) => set("model", e.target.value)}>
            {models.map((m) => (
              <option key={m}>{m}</option>
            ))}
          </select>
        </label>
        <label>
          Input source
          <select value={cfg.input_kind} onChange={(e) => set("input_kind", e.target.value as InputKind)}>
            <option value="email">email (pasted or .eml)</option>
            <option value="document">uploaded document</option>
          </select>
        </label>
      </div>
      <label>
        System prompt
        <textarea rows={2} value={cfg.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} />
      </label>
      <p className="hint">
        Prompt variables: {"{{input}}"}, {"{{previous}}"}, {"{{step.<name>}}"}, {"{{subject}}"}, {"{{sender}}"},{" "}
        {"{{filename}}"}, {"{{client}}"}
      </p>
      {cfg.steps.map((step, i) => (
        <fieldset key={i} className="step">
          <legend>Step {i + 1}</legend>
          <label>
            Step name
            <input
              aria-label={`step ${i + 1} name`}
              value={step.name}
              onChange={(e) => setStep(i, { name: e.target.value })}
              required
            />
          </label>
          <label>
            Prompt
            <textarea
              aria-label={`step ${i + 1} prompt`}
              rows={3}
              value={step.prompt}
              onChange={(e) => setStep(i, { prompt: e.target.value })}
              required
            />
          </label>
          {cfg.steps.length > 1 && (
            <button type="button" onClick={() => set("steps", cfg.steps.filter((_, j) => j !== i))}>
              Remove step {i + 1}
            </button>
          )}
        </fieldset>
      ))}
      <div className="row">
        <button
          type="button"
          onClick={() =>
            set("steps", [...cfg.steps, { name: `step${cfg.steps.length + 1}`, prompt: "{{previous}}" }])
          }
        >
          Add step
        </button>
        <button type="submit" disabled={busy}>
          {submitLabel}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </form>
  );
}
