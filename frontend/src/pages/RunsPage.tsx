import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import Badge from "../components/Badge";
import ErrorBox from "../components/ErrorBox";
import type { AuditRun } from "../types";

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

export default function RunsPage() {
  const [runs, setRuns] = useState<AuditRun[]>([]);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  const load = useCallback(() => {
    api
      .runs()
      .then((data) => {
        setRuns(data);
        setError("");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  // While a run is queued or running, refresh every 3 seconds to show progress.
  const active = runs.some((r) => r.status === "queued" || r.status === "running");
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [active, load]);

  async function start() {
    setStarting(true);
    try {
      await api.startRun();
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-indigo-900">Audit runs</h1>
        <button
          type="button"
          onClick={start}
          disabled={starting || active}
          className="rounded bg-linear-to-r from-indigo-600 to-violet-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:from-indigo-700 hover:to-violet-700 disabled:opacity-50"
        >
          {active ? "Run in progress…" : "Start new audit"}
        </button>
      </div>
      <p className="text-sm text-slate-600">
        Runs started here are processed by the background worker. Keep it running in a terminal:{" "}
        <code className="rounded bg-indigo-50 px-1 text-indigo-700">python manage.py run_worker</code>
      </p>

      {error && <ErrorBox message={error} />}

      <div className="overflow-x-auto rounded-lg border border-indigo-100 bg-white shadow-sm">
        <table className="w-full text-left text-sm">
          <thead className="bg-indigo-50 text-xs uppercase text-indigo-700">
            <tr>
              <th className="px-3 py-2">Run</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Progress</th>
              <th className="px-3 py-2">Pairs judged / cached / investigated</th>
              <th className="px-3 py-2">Findings</th>
              <th className="px-3 py-2">Coverage</th>
              <th className="px-3 py-2">Cost $</th>
              <th className="px-3 py-2">Started</th>
              <th className="px-3 py-2">Model</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-4 text-slate-500">
                  No runs yet.
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-slate-100 align-top">
                <td className="px-3 py-2">{r.id}</td>
                <td className="px-3 py-2">
                  <Badge value={r.status} />
                  {r.status === "failed" && (
                    <p className="mt-1 max-w-xs whitespace-pre-wrap text-xs text-red-700">{r.error.split("\n")[0]}</p>
                  )}
                </td>
                <td className="px-3 py-2">
                  {r.progress.stage ?? "—"}
                  {r.progress.pairs_total ? ` (${r.progress.pairs_done ?? 0}/${r.progress.pairs_total})` : ""}
                </td>
                <td className="px-3 py-2">
                  {r.totals.judged ?? 0} / {r.totals.from_cache ?? 0} / {r.totals.investigated ?? 0}
                </td>
                <td className="px-3 py-2">
                  {r.totals.findings ?? 0}
                  {(r.totals.findings_fixed ?? 0) > 0 && (
                    <p className="text-xs text-green-700">{r.totals.findings_fixed} from the previous audit fixed</p>
                  )}
                  {(r.totals.decisions_reused ?? 0) > 0 && (
                    <p className="text-xs text-slate-500">{r.totals.decisions_reused} with an earlier reviewer decision</p>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-slate-600">
                  {/* What this audit did and did NOT check, so nothing is cut silently. */}
                  <p>new documents: {r.totals.new_documents ?? 0}</p>
                  <p>pairs found: {r.totals.pairs_found ?? 0}</p>
                  <p className={(r.totals.pairs_cut_by_cap ?? 0) > 0 ? "font-medium text-amber-700" : ""}>
                    cut by the pair limit: {r.totals.pairs_cut_by_cap ?? 0}
                  </p>
                  <p>skipped, no shared audience: {r.totals.pairs_skipped_no_shared_audience ?? 0}</p>
                  <p>re-checked open findings: {r.totals.pairs_rechecked ?? 0}</p>
                  <p>resolver answers from cache: {r.totals.resolver_from_cache ?? 0}</p>
                </td>
                <td className="px-3 py-2">{(r.totals.cost_usd ?? 0).toFixed(4)}</td>
                <td className="px-3 py-2">{formatTime(r.started_at)}</td>
                <td className="px-3 py-2 text-xs text-slate-600">{r.model_id || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
