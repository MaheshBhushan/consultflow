import { formatCost, formatLatency, type Run } from "./api";

export function RunDetail({ run }: { run: Run }) {
  return (
    <section className="card" aria-label={`run ${run.id}`}>
      <h3>
        Run #{run.id} <span className={`status ${run.status}`}>{run.status}</span>
      </h3>
      <p>
        Agent snapshot: {run.agent_snapshot.name} ({run.agent_snapshot.provider}/{run.agent_snapshot.model}) · total
        cost {formatCost(run.total_cost_usd)} · total latency {formatLatency(run.total_latency_ms)} · tokens{" "}
        {run.total_input_tokens} in / {run.total_output_tokens} out
      </p>
      {run.error && <p className="error">{run.error}</p>}
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Step</th>
            <th>Status</th>
            <th>Model</th>
            <th>Sources</th>
            <th>Tokens in/out</th>
            <th>Cost</th>
            <th>Latency</th>
          </tr>
        </thead>
        <tbody>
          {run.steps.map((s) => (
            <tr key={s.id}>
              <td>{s.position}</td>
              <td>{s.name}</td>
              <td>{s.status}</td>
              <td>
                {s.provider}/{s.model}
              </td>
              <td>
                {s.sources.map((src) => (
                  <code key={src}>{src}</code>
                ))}
              </td>
              <td>
                {s.input_tokens}/{s.output_tokens}
              </td>
              <td>{formatCost(s.cost_usd)}</td>
              <td>{formatLatency(s.latency_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {run.steps.map((s) => (
        <details key={s.id}>
          <summary>
            Step {s.position} {s.name}: prompt and output
          </summary>
          <h4>Prompt</h4>
          <pre>{s.prompt}</pre>
          <h4>Output</h4>
          <pre>{s.output ?? s.error}</pre>
        </details>
      ))}
      {run.final_output && (
        <>
          <h4>Final output</h4>
          <pre data-testid="final-output">{run.final_output}</pre>
        </>
      )}
    </section>
  );
}
