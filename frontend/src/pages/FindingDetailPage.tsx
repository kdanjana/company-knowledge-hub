import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import Badge from "../components/Badge";
import ErrorBox from "../components/ErrorBox";
import FeedbackButtons from "../components/FeedbackButtons";
import Highlight from "../components/Highlight";
import type { ChunkInfo, FeedbackChoice, FindingDetail } from "../types";

function Passage({
  name,
  chunk,
  quote,
  isTruth,
  owner,
  isNew,
  isWinner,
}: {
  name: string;
  chunk: ChunkInfo | null;
  quote: string;
  isTruth: boolean;
  owner: string;
  isNew: boolean;
  isWinner: boolean;
}) {
  if (!chunk) {
    return (
      <div className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
        Passage {name} is no longer in the knowledge base (it changed after this run).
      </div>
    );
  }
  const doc = chunk.document;
  return (
    <div className={`rounded-lg border p-4 text-sm shadow-sm ${isTruth ? "border-green-300 bg-green-50" : "border-indigo-100 bg-white"}`}>
      <p className="font-medium">
        Passage {name}
        {isTruth && <span className="ml-2 text-green-700">suggested truth</span>}
        {isWinner && <span className="ml-2 text-green-800">✔ chosen by auditor</span>}
      </p>
      {isNew && !isWinner && (
        <p className="mt-1 text-xs font-medium text-amber-700">
          New text: the chat does not use it until an auditor decides.
        </p>
      )}
      {doc && (
        <p className="mt-1 text-xs text-slate-500">
          {doc.path} · {doc.doc_type || "type ?"} · v{doc.version || "?"} · {doc.effective_date ?? "no date"} ·{" "}
          {doc.status || "status ?"}
        </p>
      )}
      <p className="mt-1 text-xs text-slate-500">Owner: {owner || "unknown"}</p>
      <p className="mt-1 text-xs text-slate-500">Section: {chunk.heading_path || "(none)"}</p>
      <p className="mt-3 whitespace-pre-wrap leading-relaxed">
        <Highlight text={chunk.text} quote={quote} />
      </p>
    </div>
  );
}

export default function FindingDetailPage() {
  const { id } = useParams();
  const [finding, setFinding] = useState<FindingDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .finding(Number(id))
      .then(setFinding)
      .catch((e: Error) => setError(e.message));
  }, [id]);

  async function sendFeedback(choice: FeedbackChoice) {
    if (!finding) return;
    try {
      const updated = await api.feedback(finding.id, choice);
      setFinding({ ...finding, status: updated.status, winner: updated.winner });
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error) return <ErrorBox message={error} />;
  if (!finding) return <p className="text-sm text-slate-500">Loading…</p>;

  return (
    <div className="space-y-6">
      <Link to="/findings" className="text-sm text-indigo-700 hover:underline">
        ← All findings
      </Link>

      <div className="flex flex-wrap items-center gap-2">
        <Badge value={finding.severity} />
        <Badge value={finding.kind} />
        <Badge value={finding.status} />
        <h1 className="text-xl font-semibold text-indigo-900">{finding.topic || "(no topic)"}</h1>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Passage
          name="A"
          chunk={finding.chunk_a}
          quote={finding.quote_a}
          isTruth={finding.suggested_truth === "a"}
          owner={finding.owner_a}
          isNew={finding.new_side === "a" || finding.new_side === "both"}
          isWinner={finding.winner === "a"}
        />
        <Passage
          name="B"
          chunk={finding.chunk_b}
          quote={finding.quote_b}
          isTruth={finding.suggested_truth === "b"}
          owner={finding.owner_b}
          isNew={finding.new_side === "b" || finding.new_side === "both"}
          isWinner={finding.winner === "b"}
        />
      </div>

      <section className="rounded-lg border border-indigo-100 border-t-4 border-t-indigo-400 bg-white p-4 text-sm shadow-sm">
        <h2 className="font-medium">Why it was flagged</h2>
        <p className="mt-1">{finding.reason}</p>
        {finding.kind === "contradiction" && (
          <>
            <h2 className="mt-4 font-medium">Which one is the truth?</h2>
            <p className="mt-1">
              {finding.suggested_truth ? `Passage ${finding.suggested_truth.toUpperCase()}` : "Cannot be decided"}
              {finding.resolution_rule && <span className="text-slate-500"> (decided by: {finding.resolution_rule})</span>}
            </p>
            <p className="mt-1 text-slate-600">{finding.resolution_reason}</p>
          </>
        )}
        {finding.suggested_action && (
          <>
            <h2 className="mt-4 font-medium">Suggested action</h2>
            <p className="mt-1">{finding.suggested_action}</p>
          </>
        )}
        <div className="mt-4">
          <FeedbackButtons kind={finding.kind} onSend={sendFeedback} />
        </div>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Decision history</h2>
        <div className="overflow-x-auto rounded-lg border border-indigo-100 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="bg-indigo-50 text-xs uppercase text-indigo-700">
              <tr>
                <th className="px-3 py-2">Stage</th>
                <th className="px-3 py-2">Label</th>
                <th className="px-3 py-2">Confidence</th>
                <th className="px-3 py-2">Grounded</th>
                <th className="px-3 py-2">Tokens in/out</th>
                <th className="px-3 py-2">Cost $</th>
                <th className="px-3 py-2">Reason</th>
              </tr>
            </thead>
            <tbody>
              {finding.verdicts.map((v) => (
                <tr key={v.id} className="border-t border-slate-100 align-top">
                  <td className="px-3 py-2">{v.stage}</td>
                  <td className="px-3 py-2">{v.label}</td>
                  <td className="px-3 py-2">{v.confidence}</td>
                  <td className="px-3 py-2">{v.grounded ? "yes" : "no"}</td>
                  <td className="px-3 py-2">
                    {v.input_tokens}/{v.output_tokens}
                  </td>
                  <td className="px-3 py-2">{v.cost_usd.toFixed(5)}</td>
                  <td className="px-3 py-2">{v.reason || v.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {finding.investigation && (
        <section>
          <h2 className="mb-2 font-medium">
            Investigator agent ({finding.investigation.tool_calls} tool calls → {finding.investigation.final_label})
          </h2>
          {finding.investigation.error && <p className="mb-2 text-sm text-red-700">{finding.investigation.error}</p>}
          <ol className="space-y-2">
            {finding.investigation.steps.map((step, i) => (
              <li key={i} className="rounded-lg border border-indigo-100 bg-white p-3 text-sm shadow-sm">
                {step.type === "model" ? (
                  <>
                    <p className="text-xs font-medium text-indigo-700">Model</p>
                    {step.text && <p className="mt-1 whitespace-pre-wrap">{step.text}</p>}
                    {step.tool_requests && step.tool_requests.length > 0 && (
                      <p className="mt-1 text-slate-600">Asked for: {step.tool_requests.join(", ")}</p>
                    )}
                  </>
                ) : (
                  <>
                    <p className="text-xs font-medium text-amber-700">Tool: {step.name}</p>
                    <p className="mt-1 text-slate-600">Input: {JSON.stringify(step.input)}</p>
                    <p className="mt-1 break-words font-mono text-xs text-slate-700">{step.output_preview}</p>
                  </>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}
