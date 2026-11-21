import { useEffect, useState, type ChangeEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import Badge from "../components/Badge";
import ErrorBox from "../components/ErrorBox";
import FeedbackButtons from "../components/FeedbackButtons";
import type { AuditRun, FeedbackChoice, Finding } from "../types";

const selectStyle = "rounded border border-indigo-200 bg-white px-2 py-1 text-sm";

export default function FindingsPage() {
  const [runs, setRuns] = useState<AuditRun[]>([]);
  const [filters, setFilters] = useState({ run: "", severity: "", kind: "", status: "" });
  const [findings, setFindings] = useState<Finding[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .runs()
      .then((all) => {
        const done = all.filter((r) => r.status === "done");
        setRuns(done);
        if (done.length > 0) setFilters((f) => ({ ...f, run: String(done[0].id) }));
        else setLoading(false);
      })
      .catch((e: Error) => {
        setError(e.message);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!filters.run) return;
    setLoading(true);
    api
      .findings(filters)
      .then((data) => {
        setFindings(data);
        setError("");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filters]);

  async function sendFeedback(id: number, choice: FeedbackChoice) {
    try {
      const updated = await api.feedback(id, choice);
      setFindings((list) => list.map((f) => (f.id === id ? updated : f)));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const update = (key: keyof typeof filters) => (e: ChangeEvent<HTMLSelectElement>) =>
    setFilters((f) => ({ ...f, [key]: e.target.value }));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-xl font-semibold text-indigo-900">Findings</h1>
        <div className="flex flex-wrap gap-2">
          <select className={selectStyle} value={filters.run} onChange={update("run")}>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                Run {r.id}
              </option>
            ))}
          </select>
          <select className={selectStyle} value={filters.severity} onChange={update("severity")}>
            <option value="">All severities</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="info">Info</option>
          </select>
          <select className={selectStyle} value={filters.kind} onChange={update("kind")}>
            <option value="">All kinds</option>
            <option value="contradiction">Contradictions</option>
            <option value="duplicate">Duplicates</option>
          </select>
          <select className={selectStyle} value={filters.status} onChange={update("status")}>
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="confirmed">Confirmed</option>
            <option value="rejected">Rejected</option>
            <option value="ignored">Ignored</option>
            <option value="fixed">Fixed (by a later audit)</option>
          </select>
        </div>
      </div>

      {error && <ErrorBox message={error} />}
      {loading && <p className="text-sm text-slate-500">Loading…</p>}
      {!loading && runs.length === 0 && !error && (
        <p className="text-sm text-slate-600">No finished runs yet. Start one on the Runs page.</p>
      )}
      {!loading && runs.length > 0 && findings.length === 0 && (
        <p className="text-sm text-slate-600">No findings match these filters.</p>
      )}

      <ul className="space-y-3">
        {findings.map((f) => (
          <li key={f.id} className="rounded-lg border border-indigo-100 border-t-4 border-t-indigo-400 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge value={f.severity} />
              <Badge value={f.kind} />
              <Badge value={f.status} />
              <span className="font-medium">{f.topic || "(no topic)"}</span>
              <Link to={`/findings/${f.id}`} className="ml-auto text-sm text-indigo-700 hover:underline">
                Details →
              </Link>
            </div>

            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {(["a", "b"] as const).map((side) => {
                const isNew = f.new_side === side || f.new_side === "both";
                const owner = side === "a" ? f.owner_a : f.owner_b;
                return (
                  <div
                    key={side}
                    className={`rounded border p-3 text-sm ${
                      f.suggested_truth === side ? "border-green-300 bg-green-50" : "border-slate-200"
                    }`}
                  >
                    <p className="text-xs text-slate-500">
                      {side.toUpperCase()} · {side === "a" ? f.doc_a_path : f.doc_b_path}
                      {owner && <> · owner: {owner}</>}
                      {f.suggested_truth === side && <span className="ml-2 font-medium text-green-700">suggested truth</span>}
                      {f.winner === side && <span className="ml-2 font-medium text-green-800">✔ chosen by auditor</span>}
                    </p>
                    {isNew && !f.winner && (
                      <p className="mt-1 text-xs font-medium text-amber-700">New text: not used in chat until you decide</p>
                    )}
                    <p className="mt-1">“{side === "a" ? f.quote_a : f.quote_b}”</p>
                  </div>
                );
              })}
            </div>

            <p className="mt-3 text-sm text-slate-700">{f.reason}</p>
            {f.suggested_action && <p className="mt-1 text-sm text-slate-600">→ {f.suggested_action}</p>}

            <div className="mt-3">
              <FeedbackButtons kind={f.kind} onSend={(choice) => sendFeedback(f.id, choice)} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
