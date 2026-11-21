import { useEffect, useState } from "react";
import { api } from "../api";
import ErrorBox from "../components/ErrorBox";
import type { EvalResult } from "../types";

const PERCENT_KEYS = ["candidate_recall", "recall", "precision", "trap_false_alarm_rate", "resolver_accuracy"];

const COLUMNS: { key: string; label: string }[] = [
  { key: "candidate_recall", label: "Candidate recall" },
  { key: "recall", label: "Recall" },
  { key: "precision", label: "Precision" },
  { key: "trap_false_alarm_rate", label: "Trap false alarms" },
  { key: "resolver_accuracy", label: "Resolver accuracy" },
  { key: "unexpected_findings", label: "Unexpected findings" },
  { key: "cost_usd", label: "Cost $" },
  { key: "duration_seconds", label: "Time (s)" },
];

function formatMetric(key: string, value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (PERCENT_KEYS.includes(key)) return `${Math.round(value * 100)}%`;
  if (key === "cost_usd") return value.toFixed(4);
  return String(value);
}

const OUTCOME_STYLE: Record<string, string> = {
  found: "text-green-700",
  correctly_ignored: "text-green-700",
  missed_by_judge: "text-red-700",
  missed_not_a_candidate: "text-red-700",
  false_alarm: "text-red-700",
  wrong_kind: "text-amber-700",
  unexpected_finding: "text-amber-700",
  setup_error: "text-red-700",
};

export default function EvaluationPage() {
  const [results, setResults] = useState<EvalResult[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .evaluations()
      .then(setResults)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-indigo-900">Evaluation</h1>
      <p className="text-sm text-slate-600">
        Each run compared with the answer key (<code className="rounded bg-indigo-50 px-1 text-indigo-700">data/ground_truth.yaml</code>
        ). Create one with <code className="rounded bg-indigo-50 px-1 text-indigo-700">python manage.py evaluate</code>.
      </p>
      {error && <ErrorBox message={error} />}
      {!error && results.length === 0 && <p className="text-sm text-slate-600">No evaluations yet.</p>}

      {results.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-indigo-100 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="bg-indigo-50 text-xs uppercase text-indigo-700">
              <tr>
                <th className="px-3 py-2">Run</th>
                {COLUMNS.map((c) => (
                  <th key={c.key} className="px-3 py-2">
                    {c.label}
                  </th>
                ))}
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.id} className="border-t border-slate-100">
                  <td className="px-3 py-2">{r.run}</td>
                  {COLUMNS.map((c) => (
                    <td key={c.key} className="px-3 py-2">
                      {formatMetric(c.key, r.metrics[c.key])}
                    </td>
                  ))}
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      className="text-indigo-700 hover:underline"
                      onClick={() => setOpen(open === r.id ? null : r.id)}
                    >
                      {open === r.id ? "Hide" : "Details"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {results
        .filter((r) => r.id === open)
        .map((r) => (
          <div key={r.id} className="overflow-x-auto rounded-lg border border-indigo-100 bg-white shadow-sm">
            <table className="w-full text-left text-sm">
              <thead className="bg-indigo-50 text-xs uppercase text-indigo-700">
                <tr>
                  <th className="px-3 py-2">ID</th>
                  <th className="px-3 py-2">Kind</th>
                  <th className="px-3 py-2">Topic</th>
                  <th className="px-3 py-2">Outcome</th>
                  <th className="px-3 py-2">Final label</th>
                  <th className="px-3 py-2">Note</th>
                </tr>
              </thead>
              <tbody>
                {r.details.map((d) => (
                  <tr key={d.id} className="border-t border-slate-100 align-top">
                    <td className="px-3 py-2 font-mono text-xs">{d.id}</td>
                    <td className="px-3 py-2">{d.kind}</td>
                    <td className="px-3 py-2">{d.topic}</td>
                    <td className={`px-3 py-2 font-medium ${OUTCOME_STYLE[d.outcome] ?? ""}`}>{d.outcome}</td>
                    <td className="px-3 py-2">{d.final_label ?? "—"}</td>
                    <td className="px-3 py-2 text-slate-600">
                      {d.note}
                      {d.resolver_correct === false && " Resolver picked the wrong source."}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}
