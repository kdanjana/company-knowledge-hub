import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import Badge from "../components/Badge";
import ErrorBox from "../components/ErrorBox";
import type { DocumentState, ManagedDocument, NamePreview, UploadForm, UploadStandards } from "../types";

type Mode = "new" | "version";

const EMPTY_FORM: UploadForm = {
  doc_id: "",
  title: "",
  doc_type: "",
  effective_date: "",
  status: "active",
  owner: "",
  region: "",
  audience: "",
  visibility: "department",
  replaces: "",
};

// What each state means for the person who uploaded the document.
const STATE_HELP: Record<DocumentState, string> = {
  processing: "Being split into sections and embedded.",
  awaiting_audit: "Waiting for the next audit. Not used in chat yet.",
  live: "Used in chat. Sections that conflict with other documents wait for an auditor.",
  awaiting_approval: "New version: an auditor must approve it. The old version is still used.",
  rejected: "An auditor rejected this version. It is not used.",
  failed: "Processing failed (see the error). Fix the file and upload it again.",
};

const inputStyle = "w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm";

/** Same rule as the backend (kb/standards.py): "Refund Policy (UAE)" -> "refund-policy-uae". */
function slugify(text: string, max = 60): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, max)
    .replace(/-+$/g, "");
}

/** Reads simple "key: value" lines from the --- block at the top of a Markdown file. */
function parseFrontMatter(text: string): Record<string, string> {
  const match = text.match(/^﻿?---\s*\r?\n([\s\S]*?)\r?\n---\s*\r?\n/);
  if (!match) return {};
  const values: Record<string, string> = {};
  for (const line of match[1].split(/\r?\n/)) {
    const kv = line.match(/^([A-Za-z_]+):\s*(.*)$/);
    if (kv && kv[2] && !kv[2].startsWith("[")) values[kv[1]] = kv[2].replace(/^["']|["']$/g, "").trim();
  }
  return values;
}

/** Returns the allowed spelling of a value (ignoring case), or "" if it is not in the list. */
function inList(value: string | undefined, list: string[]): string {
  return list.find((option) => option.toLowerCase() === (value ?? "").toLowerCase()) ?? "";
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

function Select({ value, onChange, options, empty }: {
  value: string;
  onChange: (e: ChangeEvent<HTMLSelectElement>) => void;
  options: string[];
  empty?: string;
}) {
  return (
    <select className={inputStyle} value={value} onChange={onChange}>
      {empty !== undefined && <option value="">{empty}</option>}
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

export default function UploadPage() {
  const { me } = useAuth();
  const [standards, setStandards] = useState<UploadStandards | null>(null);
  const [mode, setMode] = useState<Mode>("new");
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState<UploadForm>(EMPTY_FORM);
  const [idEdited, setIdEdited] = useState(false); // stop filling the ID from the title once the user typed one
  const [preview, setPreview] = useState<NamePreview | null>(null);
  const [docs, setDocs] = useState<ManagedDocument[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);

  const load = useCallback(() => {
    api
      .kbDocuments()
      .then(setDocs)
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  useEffect(() => {
    api
      .standards()
      .then((s) => {
        setStandards(s);
        // Owner defaults to the uploader's department.
        setForm((f) => ({ ...f, owner: f.owner || inList(me?.department, s.owners) }));
      })
      .catch((e: Error) => setError(e.message));
  }, [me?.department]);

  // Live preview of the standard name, from the server (it knows the latest version of every document).
  useEffect(() => {
    if (mode === "version" && !form.replaces) return setPreview(null);
    if (mode === "new" && !form.doc_id && !form.title) return setPreview(null);
    const timer = setTimeout(() => {
      const params = mode === "version" ? { replaces: form.replaces } : { doc_id: form.doc_id, title: form.title };
      api
        .namePreview(params)
        .then(setPreview)
        .catch(() => setPreview(null));
    }, 300);
    return () => clearTimeout(timer);
  }, [mode, form.doc_id, form.title, form.replaces]);

  const myUploads = docs.filter((d) => d.source === "upload" && d.uploaded_by === me?.username);
  const replaceable = docs.filter((d) => d.state === "live" && d.status !== "superseded");

  // Refresh while one of my uploads is still moving through processing / audit.
  const moving = myUploads.some((d) => d.state === "processing" || d.state === "awaiting_audit");
  useEffect(() => {
    if (!moving) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [moving, load]);

  function switchMode(next: Mode) {
    setMode(next);
    setForm((f) => ({ ...EMPTY_FORM, owner: f.owner, effective_date: f.effective_date, visibility: f.visibility }));
    setIdEdited(false);
    setMessage("");
  }

  function setTitle(e: ChangeEvent<HTMLInputElement>) {
    const title = e.target.value;
    setForm((f) => ({ ...f, title, doc_id: mode === "new" && !idEdited ? slugify(title) : f.doc_id }));
  }

  function setDocId(e: ChangeEvent<HTMLInputElement>) {
    setIdEdited(true);
    setForm((f) => ({ ...f, doc_id: e.target.value.toLowerCase() }));
  }

  // New version: copy the details of the document it replaces; the user changes only what is different.
  function chooseReplaced(e: ChangeEvent<HTMLSelectElement>) {
    const old = docs.find((d) => String(d.id) === e.target.value);
    if (!old || !standards) return setForm((f) => ({ ...f, replaces: e.target.value }));
    setForm((f) => ({
      ...f,
      replaces: String(old.id),
      title: old.title,
      doc_type: inList(old.doc_type, standards.doc_types),
      owner: inList(old.owner, standards.owners) || f.owner,
      region: inList(old.region, standards.regions),
      audience: inList(old.audience, standards.audiences),
      visibility: old.departments.length ? "department" : "everyone",
    }));
  }

  async function chooseFile(e: ChangeEvent<HTMLInputElement>) {
    const chosen = e.target.files?.[0] ?? null;
    setFile(chosen);
    setMessage("");
    if (!chosen || !standards) return;
    // Pre-fill empty fields from the file's front-matter. Values typed by the user are kept.
    const meta = parseFrontMatter(await chosen.text());
    if (mode === "new" && meta.doc_id && !form.doc_id) setIdEdited(true);
    setForm((f) => ({
      ...f,
      doc_id: mode === "new" ? f.doc_id || (meta.doc_id ?? "") || slugify(f.title || meta.title || "") : f.doc_id,
      title: f.title || meta.title || "",
      doc_type: f.doc_type || inList(meta.doc_type, standards.doc_types),
      effective_date: f.effective_date || meta.effective_date || "",
      status: meta.status === "draft" ? "draft" : f.status,
      owner: f.owner || inList(meta.owner, standards.owners),
      region: f.region || inList(meta.region, standards.regions),
      audience: f.audience || inList(meta.audience, standards.audiences),
    }));
  }

  const set = (key: keyof UploadForm) => (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setSending(true);
    setError("");
    setMessage("");
    try {
      const payload = mode === "version" ? { ...form, doc_id: "" } : { ...form, replaces: "" };
      const doc = await api.uploadDocument(file, payload);
      setMessage(`Uploaded as ${doc.path}. It is being processed; an audit runs automatically afterwards.`);
      setFile(null);
      switchMode(mode);
      (e.target as HTMLFormElement).reset();
      load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  }

  if (!standards) return error ? <ErrorBox message={error} /> : <p className="text-sm text-slate-500">Loading…</p>;
  const canSend = !!file && !sending && !!preview?.name && !preview.error;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-indigo-900">Upload a document</h1>
        <p className="mt-1 text-sm text-slate-600">
          Markdown (.md) only. You can upload a file with <b>any name</b>: it is saved as{" "}
          <code className="rounded bg-indigo-50 px-1 text-indigo-700">document-id__v&lt;version&gt;.md</code>. The Document ID stays the
          same for all versions of a document; the version number is set automatically.
        </p>
        <p className="mt-1 text-sm text-slate-600">
          A new document is checked by an audit before the chat uses it. Sections that conflict with existing documents
          are not used until an auditor decides. A new version always needs an auditor's approval.
        </p>
      </div>

      {error && <ErrorBox message={error} />}
      {message && <p className="rounded border border-green-200 bg-green-50 p-3 text-sm text-green-800">{message}</p>}

      <div className="flex gap-2 text-sm">
        {(["new", "version"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => switchMode(m)}
            className={`rounded-full border px-3 py-1 ${
              mode === m
                ? "border-transparent bg-linear-to-r from-indigo-600 to-violet-600 text-white shadow-sm"
                : "border-indigo-200 bg-white text-indigo-800 hover:bg-indigo-50"
            }`}
          >
            {m === "new" ? "New document" : "New version of an existing document"}
          </button>
        ))}
      </div>

      <form
        onSubmit={submit}
        className="grid gap-4 rounded-lg border border-indigo-100 border-t-4 border-t-indigo-400 bg-white p-4 shadow-sm md:grid-cols-2"
      >
        <div className="md:col-span-2">
          <Field label="File (.md)" hint="Empty fields below are filled from the file's front-matter when it has one.">
            <input type="file" accept=".md,text/markdown" onChange={chooseFile} required className="text-sm" />
          </Field>
        </div>

        {mode === "version" ? (
          <div className="md:col-span-2">
            <Field label="New version of *" hint="The new version keeps this document's ID; details below are copied from it.">
              <select className={inputStyle} value={form.replaces} onChange={chooseReplaced} required>
                <option value="">(choose a document)</option>
                {replaceable.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title} · {d.doc_id || d.path} · v{d.version || "?"}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        ) : null}

        <Field label="Title *">
          <input className={inputStyle} value={form.title} onChange={setTitle} required maxLength={standards.title_max} />
        </Field>

        {mode === "new" && (
          <Field
            label="Document ID *"
            hint={`Made from the title. Lowercase letters, digits and hyphens, at most ${standards.doc_id_max} characters.`}
          >
            <input className={`${inputStyle} font-mono`} value={form.doc_id} onChange={setDocId} required placeholder="refund-policy-uae" />
          </Field>
        )}

        <Field label="Type *">
          <Select value={form.doc_type} onChange={set("doc_type")} options={standards.doc_types} empty="(choose)" />
        </Field>
        <Field label="Owner (team responsible) *">
          <Select value={form.owner} onChange={set("owner")} options={standards.owners} empty="(choose)" />
        </Field>
        <Field label="Effective date *">
          <input type="date" className={inputStyle} value={form.effective_date} onChange={set("effective_date")} required />
        </Field>
        <Field label="Status *">
          <Select value={form.status} onChange={set("status")} options={standards.statuses} />
        </Field>
        <Field label="Region" hint="Optional. Helps the audit tell 'India: 3 days' and 'UAE: 5 days' apart.">
          <Select value={form.region} onChange={set("region")} options={standards.regions} empty="(not set)" />
        </Field>
        <Field label="Audience" hint="Optional. E.g. employees vs delivery partners.">
          <Select value={form.audience} onChange={set("audience")} options={standards.audiences} empty="(not set)" />
        </Field>
        <Field label="Who can see it in chat">
          <select className={inputStyle} value={form.visibility} onChange={set("visibility")}>
            <option value="department">Only my department{me?.department ? ` (${me.department})` : ""}</option>
            <option value="everyone">Everyone in the company</option>
          </select>
        </Field>

        <div className="rounded border border-indigo-100 bg-indigo-50/60 p-3 text-sm md:col-span-2">
          <span className="text-slate-600">Will be saved as: </span>
          {preview?.name ? (
            <code className="font-medium">{preview.name}</code>
          ) : preview?.error ? (
            <span className="text-red-700">{preview.error}</span>
          ) : (
            <span className="text-slate-400">{mode === "version" ? "choose a document" : "enter a title"}</span>
          )}
        </div>

        <div className="md:col-span-2">
          <button
            type="submit"
            disabled={!canSend}
            className="rounded bg-linear-to-r from-indigo-600 to-violet-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:from-indigo-700 hover:to-violet-700 disabled:opacity-50"
          >
            {sending ? "Uploading…" : "Upload"}
          </button>
        </div>
      </form>

      <section>
        <h2 className="mb-2 font-medium text-indigo-900">My uploads</h2>
        {myUploads.length === 0 ? (
          <p className="text-sm text-slate-500">You have not uploaded anything yet.</p>
        ) : (
          <ul className="space-y-2">
            {myUploads.map((d) => (
              <li key={d.id} className="rounded-lg border border-indigo-100 border-l-4 border-l-violet-400 bg-white p-3 text-sm shadow-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge value={d.state} />
                  <code className="font-medium">{d.path}</code>
                  <span>{d.title}</span>
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  uploaded file: {d.original_name} · {d.departments.length ? d.departments.join(", ") : "Everyone"}
                  {d.uploaded_at ? ` · ${new Date(d.uploaded_at).toLocaleString()}` : ""}
                </p>
                <p className="mt-1 text-slate-600">{STATE_HELP[d.state]}</p>
                {d.error && <p className="mt-1 text-xs text-red-700">{d.error}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
