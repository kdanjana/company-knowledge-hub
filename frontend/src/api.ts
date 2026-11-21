import type {
  AuditRun,
  ChatStreamEvent,
  Conversation,
  ConversationDetail,
  EvalResult,
  FeedbackChoice,
  Finding,
  FindingDetail,
  KBDocument,
  LoginResponse,
  ManagedDocument,
  Me,
  NamePreview,
  UploadForm,
  UploadStandards,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// ---------- JWT tokens (kept in localStorage) ----------
// access: sent as "Authorization: Bearer <access>" on every API call (expires after ~15 min)
// refresh: only used to get a new access token (POST /api/auth/refresh/); each refresh returns a new one

const ACCESS_KEY = "kb_access_token";
const REFRESH_KEY = "kb_refresh_token";

function readToken(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return ""; // storage blocked (e.g. some private windows): behave as logged out
  }
}

function saveTokens(access: string, refresh: string) {
  try {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
  } catch {
    /* storage blocked: the login only lasts until the page is reloaded */
  }
}

export function clearTokens() {
  try {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* nothing stored */
  }
}

export function hasTokens(): boolean {
  return readToken(REFRESH_KEY) !== "";
}

// Called when the tokens cannot be refreshed any more (refresh token expired or logged out elsewhere).
let onSignedOut: () => void = () => {};
export function setSignedOutHandler(handler: () => void) {
  onSignedOut = handler;
}

// One refresh at a time: requests that get a 401 at the same moment share the same refresh call
// (the refresh token rotates, so two parallel refreshes would make the second one fail).
let refreshing: Promise<boolean> | null = null;

function refreshTokens(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      const refresh = readToken(REFRESH_KEY);
      if (!refresh) return false;
      try {
        const res = await fetch("/api/auth/refresh/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh }),
        });
        if (!res.ok) return false;
        const data = (await res.json()) as { access: string; refresh?: string };
        saveTokens(data.access, data.refresh ?? refresh);
        return true;
      } catch {
        return false;
      }
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

/** fetch with the access token. On 401 (access token expired): refresh once and retry. */
async function authFetch(url: string, init: RequestInit = {}, jsonBody = true): Promise<Response> {
  const send = () => {
    const headers: Record<string, string> = jsonBody ? { "Content-Type": "application/json" } : {};
    const access = readToken(ACCESS_KEY);
    if (access) headers.Authorization = `Bearer ${access}`;
    return fetch(url, { ...init, headers });
  };
  let res = await send();
  if (res.status === 401 && hasTokens()) {
    if (await refreshTokens()) {
      res = await send();
    } else {
      clearTokens();
      onSignedOut();
    }
  }
  return res;
}

async function errorFrom(res: Response): Promise<ApiError> {
  const body = await res.text();
  let message = `${res.status} ${res.statusText}`;
  try {
    const data = JSON.parse(body) as { detail?: string };
    if (data.detail) message = data.detail;
  } catch {
    if (body) message += `: ${body.slice(0, 300)}`;
  }
  if (res.status === 429) message = "Too many questions in a short time. Please wait a minute and try again.";
  return new ApiError(res.status, message);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await authFetch(`/api${path}`, init);
  if (!res.ok) throw await errorFrom(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Upload a Markdown file with its form fields (multipart: the browser sets the Content-Type itself). */
async function uploadDocument(file: File, form: UploadForm): Promise<ManagedDocument> {
  const body = new FormData();
  body.append("file", file);
  for (const [key, value] of Object.entries(form)) {
    if (value !== "") body.append(key, value);
  }
  const res = await authFetch("/api/kb/documents/", { method: "POST", body }, false);
  if (!res.ok) throw await errorFrom(res);
  return res.json() as Promise<ManagedDocument>;
}

/** POST a question and call onEvent for every streamed JSON line. */
async function streamMessage(conversationId: number, question: string, onEvent: (e: ChatStreamEvent) => void) {
  const res = await authFetch(`/api/chat/conversations/${conversationId}/messages/`, {
    method: "POST",
    body: JSON.stringify({ question }),
  });
  if (!res.ok || !res.body) throw await errorFrom(res);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as ChatStreamEvent);
      newline = buffer.indexOf("\n");
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer) as ChatStreamEvent);
}

/** Log in: saves both tokens and returns who logged in. No old token is sent. */
async function login(username: string, password: string): Promise<Me> {
  const res = await fetch("/api/auth/login/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw await errorFrom(res);
  const data = (await res.json()) as LoginResponse;
  saveTokens(data.access, data.refresh);
  return data.user;
}

/** Log out: forget the tokens here first (so the app logs out even offline), then blacklist the refresh token. */
async function logout(): Promise<void> {
  const refresh = readToken(REFRESH_KEY);
  clearTokens();
  if (!refresh) return;
  try {
    await fetch("/api/auth/logout/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh }),
    });
  } catch {
    /* the refresh token simply expires on its own */
  }
}

export const api = {
  // auth
  me: () => request<Me>("/auth/me/"),
  login,
  logout,

  // audit (auditors only)
  documents: () => request<KBDocument[]>("/documents/"),
  runs: () => request<AuditRun[]>("/runs/"),
  startRun: () => request<AuditRun>("/runs/", { method: "POST", body: "{}" }),
  findings: (params: Record<string, string>) => {
    const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== ""));
    return request<Finding[]>(`/findings/?${query.toString()}`);
  },
  finding: (id: number) => request<FindingDetail>(`/findings/${id}/`),
  feedback: (id: number, choice: FeedbackChoice, note = "") =>
    request<Finding>(`/findings/${id}/feedback/`, { method: "POST", body: JSON.stringify({ ...choice, note }) }),
  evaluations: () => request<EvalResult[]>("/evaluations/"),

  // knowledge base documents (everyone; approve / reject: auditors only)
  kbDocuments: () => request<ManagedDocument[]>("/kb/documents/"),
  standards: () => request<UploadStandards>("/kb/standards/"),
  namePreview: (params: { doc_id?: string; title?: string; replaces?: string }) => {
    const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
    return request<NamePreview>(`/kb/documents/next-name/?${query.toString()}`);
  },
  uploadDocument,
  originalText: (id: number) => request<{ id: number; path: string; text: string }>(`/kb/documents/${id}/original/`),
  approveDocument: (id: number) => request<ManagedDocument>(`/kb/documents/${id}/approve/`, { method: "POST", body: "{}" }),
  rejectDocument: (id: number) => request<ManagedDocument>(`/kb/documents/${id}/reject/`, { method: "POST", body: "{}" }),

  // chat (everyone)
  conversations: () => request<Conversation[]>("/chat/conversations/"),
  createConversation: () => request<Conversation>("/chat/conversations/", { method: "POST", body: "{}" }),
  conversation: (id: number) => request<ConversationDetail>(`/chat/conversations/${id}/`),
  deleteConversation: (id: number) => request<void>(`/chat/conversations/${id}/`, { method: "DELETE" }),
  sendMessage: streamMessage,
};
