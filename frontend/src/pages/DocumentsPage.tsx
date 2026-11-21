import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import Badge from "../components/Badge";
import ErrorBox from "../components/ErrorBox";
import type { ManagedDocument } from "../types";

const missing = <span className="text-slate-400">missing</span>;

export default function DocumentsPage() {
  const [docs, setDocs] = useState<ManagedDocument[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [original, setOriginal] = useState<{ id: number; path: string; text: string } | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    api
      .kbDocuments()
      .then((data) => {
        setDocs(data);
        setError("");
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);

  const pathOf = (id: number | null) => docs.find((d) => d.id === id)?.path ?? `document ${id}`;

  async function showOriginal(id: number) {
    try {
      setOriginal(original?.id === id ? null : await api.originalText(id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function decide(id: number, approve: boolean) {
    const question = approve
      ? "Approve this version? It goes live and the document it replaces is marked superseded."
      : "Reject this version? It will not be used.";
    if (!window.confirm(question)) return;
    setBusy(id);
    try {
      await (approve ? api.approveDocument(id) : api.rejectDocument(id));
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const waiting = docs.filter((d) => d.state === "awaiting_approval").length;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-indigo-900">Knowledge base documents</h1>
      <p className="text-sm text-slate-600">
        The company's existing documents (loaded with <code className="rounded bg-indigo-50 px-1 text-indigo-700">seed_kb</code>) and
        documents uploaded by employees. Only <b>live</b> documents that are not superseded are used by the chat.
      </p>
      {waiting > 0 && (
        <p className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          {waiting} new version(s) waiting for your approval.
        </p>
      )}
      {error && <ErrorBox message={error} />}
      {loading && <p className="text-sm text-slate-500">Loading…</p>}

      {!loading && (
        <div className="overflow-x-auto rounded-lg border border-indigo-100 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="bg-indigo-50 text-xs uppercase text-indigo-700">
              <tr>
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">State</th>
                <th className="px-3 py-2">Title</th>
                <th className="px-3 py-2">Type / version</th>
                <th className="px-3 py-2">Effective date</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Owner</th>
                <th className="px-3 py-2">Visible in chat to</th>
                <th className="px-3 py-2">Source</th>
                <th className="px-3 py-2">Chunks</th>
                <th className="px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {docs.length === 0 && (
                <tr>
                  <td colSpan={11} className="px-3 py-4 text-slate-500">
                    The knowledge base is empty.
                  </td>
                </tr>
              )}
              {docs.map((d) => (
                <tr key={d.id} className="border-t border-slate-100 align-top">
                  <td className="px-3 py-2 font-mono text-xs">
                    {d.path}
                    {d.doc_id && (
                      <p className="mt-1 font-sans text-slate-500">
                        ID {d.doc_id} · v{d.version || "?"}
                      </p>
                    )}
                    {d.source === "upload" && d.original_name && (
                      <p className="font-sans text-slate-400">uploaded file: {d.original_name}</p>
                    )}
                    {d.replaces && <p className="mt-1 font-sans text-slate-500">replaces {pathOf(d.replaces)}</p>}
                  </td>
                  <td className="px-3 py-2">
                    <Badge value={d.state} />
                    {d.error && <p className="mt-1 max-w-xs text-xs text-red-700">{d.error}</p>}
                  </td>
                  <td className="px-3 py-2">
                    {d.title}
                    {(d.region || d.audience) && (
                      <p className="mt-1 text-xs text-slate-500">
                        {[d.region && `region: ${d.region}`, d.audience && `audience: ${d.audience}`].filter(Boolean).join(" · ")}
                      </p>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {d.doc_type || missing} / {d.version || missing}
                  </td>
                  <td className="px-3 py-2">{d.effective_date ?? missing}</td>
                  <td className="px-3 py-2">{d.status || missing}</td>
                  <td className="px-3 py-2">{d.owner || missing}</td>
                  <td className="px-3 py-2">{d.departments.length ? d.departments.join(", ") : "Everyone"}</td>
                  <td className="px-3 py-2 text-xs">
                    {d.source === "upload" ? `upload by ${d.uploaded_by ?? "?"}` : "existing KB"}
                  </td>
                  <td className="px-3 py-2">{d.chunk_count}</td>
                  <td className="space-y-1 px-3 py-2">
                    <button type="button" onClick={() => showOriginal(d.id)} className="block text-indigo-700 hover:underline">
                      {original?.id === d.id ? "Hide original" : "View original"}
                    </button>
                    {d.state === "awaiting_approval" && (
                      <>
                        <button
                          type="button"
                          disabled={busy === d.id}
                          onClick={() => decide(d.id, true)}
                          className="block text-green-700 hover:underline disabled:opacity-50"
                        >
                          Approve
                        </button>
                        <button
                          type="button"
                          disabled={busy === d.id}
                          onClick={() => decide(d.id, false)}
                          className="block text-red-700 hover:underline disabled:opacity-50"
                        >
                          Reject
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {original && (
        <section className="rounded-lg border border-indigo-100 border-t-4 border-t-indigo-400 bg-white p-4 shadow-sm">
          <h2 className="font-medium text-indigo-900">Original: {original.path}</h2>
          <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-indigo-50/60 p-3 text-xs">{original.text}</pre>
        </section>
      )}
    </div>
  );
}
