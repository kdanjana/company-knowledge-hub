import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import type { ChatMessage, Conversation } from "../types";

function Warnings({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2 space-y-1">
      {items.map((w, i) => (
        <p key={i} className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          ⚠ {w}
        </p>
      ))}
    </div>
  );
}

function AssistantMessage({ message }: { message: ChatMessage }) {
  return (
    <div className="max-w-3xl rounded-lg border border-indigo-100 border-l-4 border-l-violet-400 bg-white px-4 py-3 text-sm shadow-sm">
      {message.content && <p className="whitespace-pre-wrap leading-relaxed">{message.content}</p>}
      {message.error && <p className="mt-1 text-red-700">The assistant could not answer: {message.error}</p>}
      <Warnings items={message.warnings} />
      {message.dropped.length > 0 && (
        // Not used for this answer: outdated passages, and new passages still waiting for an auditor's review.
        <details className="mt-2 text-xs text-slate-500">
          <summary className="cursor-pointer">Not used: {message.dropped.length} passage(s)</summary>
          <ul className="mt-1 space-y-1">
            {message.dropped.map((d, i) => (
              <li key={i}>
                {d.doc_path}
                {d.section ? ` · ${d.section}` : ""}: {d.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      {message.citations.length > 0 && (
        <div className="mt-3 space-y-1">
          <p className="text-xs font-medium text-indigo-700">Sources</p>
          {message.citations.map((c) => (
            <details key={c.number} className="rounded border border-indigo-100 bg-indigo-50/60 px-3 py-1 text-xs">
              <summary className="cursor-pointer">
                [{c.number}] {c.doc_title} · {c.section || c.doc_path}
              </summary>
              <p className="mt-1 whitespace-pre-wrap text-slate-700">{c.text}</p>
              <p className="mt-1 text-slate-500">{c.doc_path}</p>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streamText, setStreamText] = useState("");
  const [streamWarnings, setStreamWarnings] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(() => {
    api
      .conversations()
      .then(setConversations)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(loadConversations, [loadConversations]);
  // Braces matter: the effect must return nothing. Without them it would return what scrollIntoView
  // returns (a Promise in newer browsers), React would call it as a clean-up function and crash.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamText]);

  async function openConversation(id: number) {
    if (sending) return;
    setError("");
    setActiveId(id);
    try {
      setMessages((await api.conversation(id)).messages);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function newChat() {
    if (sending) return;
    setActiveId(null);
    setMessages([]);
    setError("");
  }

  async function removeConversation(id: number) {
    if (sending) return;
    try {
      await api.deleteConversation(id);
      if (id === activeId) newChat();
      loadConversations();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    const question = draft.trim();
    if (!question || sending) return;
    setSending(true);
    setError("");
    setDraft("");
    setStreamText("");
    setStreamWarnings([]);
    try {
      let id = activeId;
      if (id === null) {
        id = (await api.createConversation()).id;
        setActiveId(id);
      }
      const pending: ChatMessage = {
        id: -Date.now(),
        role: "user",
        content: question,
        citations: [],
        warnings: [],
        dropped: [],
        error: "",
        created_at: new Date().toISOString(),
      };
      setMessages((m) => [...m, pending]);

      await api.sendMessage(id, question, (event) => {
        if (event.type === "context") setStreamWarnings(event.warnings);
        else if (event.type === "delta") setStreamText((t) => t + event.text);
        else if (event.type === "error") setError(event.message);
        else if (event.type === "done") {
          setMessages((m) => [...m, event.message]);
          setStreamText("");
          setStreamWarnings([]);
        }
      });
      loadConversations();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="grid gap-4 md:grid-cols-[220px_1fr]">
      <aside className="space-y-2">
        <button
          type="button"
          onClick={newChat}
          className="w-full rounded bg-linear-to-r from-indigo-600 to-violet-600 px-3 py-2 text-sm font-medium text-white shadow-sm hover:from-indigo-700 hover:to-violet-700"
        >
          + New chat
        </button>
        <ul className="space-y-1 text-sm">
          {conversations.map((c) => (
            <li key={c.id} className="group flex items-center gap-1">
              <button
                type="button"
                onClick={() => openConversation(c.id)}
                className={`flex-1 truncate rounded px-2 py-1 text-left ${
                  c.id === activeId ? "bg-indigo-100 font-medium text-indigo-800" : "hover:bg-indigo-50"
                }`}
                title={c.title}
              >
                {c.title || "New conversation"}
              </button>
              <button
                type="button"
                onClick={() => removeConversation(c.id)}
                className="px-1 text-slate-400 hover:text-red-600"
                aria-label="Delete conversation"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-h-[70vh] flex-col overflow-hidden rounded-lg border border-indigo-100 bg-white/60 shadow-sm">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {messages.length === 0 && !sending && (
            <div className="text-sm text-slate-500">
              <p className="font-medium text-indigo-900">Ask about company policies and processes.</p>
              <p className="mt-1">For example: "How many days do customers have to request a refund in India?"</p>
              <p className="mt-1">Answers only use documents you have access to, and list their sources.</p>
            </div>
          )}

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <p className="max-w-2xl whitespace-pre-wrap rounded-lg bg-linear-to-r from-indigo-600 to-violet-600 px-4 py-2 text-sm text-white shadow-sm">
                  {m.content}
                </p>
              </div>
            ) : (
              <AssistantMessage key={m.id} message={m} />
            ),
          )}

          {sending && (
            <div className="max-w-3xl rounded-lg border border-indigo-100 border-l-4 border-l-violet-400 bg-white px-4 py-3 text-sm shadow-sm">
              <p className="whitespace-pre-wrap leading-relaxed">
                {streamText || <span className="text-slate-400">Searching the documents…</span>}
                <span className="ml-0.5 inline-block w-2 animate-pulse">▍</span>
              </p>
              <Warnings items={streamWarnings} />
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {error && <p className="border-t border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800">{error}</p>}

        <form onSubmit={send} className="flex gap-2 border-t border-indigo-100 bg-white p-3">
          <input
            className="flex-1 rounded border border-indigo-200 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200"
            placeholder="Type your question…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={2000}
            disabled={sending}
          />
          <button
            type="submit"
            disabled={sending || !draft.trim()}
            className="rounded bg-linear-to-r from-indigo-600 to-violet-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:from-indigo-700 hover:to-violet-700 disabled:opacity-50"
          >
            Send
          </button>
        </form>
      </section>
    </div>
  );
}
