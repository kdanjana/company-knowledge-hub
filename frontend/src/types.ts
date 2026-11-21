export type DocumentState = "processing" | "awaiting_audit" | "live" | "awaiting_approval" | "rejected" | "failed";

export interface KBDocument {
  id: number;
  path: string;
  title: string;
  doc_type: string;
  version: string;
  effective_date: string | null;
  status: string;
  owner: string;
  chunk_count: number;
  departments: string[]; // empty = everyone
  source?: "folder" | "upload";
  state?: DocumentState;
}

/** A document from /api/kb/documents/ (includes upload and life-cycle details). */
export interface ManagedDocument extends KBDocument {
  doc_id: string; // stable ID shared by all versions ("" for most seeded documents)
  region: string;
  audience: string;
  source: "folder" | "upload";
  state: DocumentState;
  uploaded_by: string | null;
  uploaded_at: string | null;
  original_name: string;
  replaces: number | null;
  error: string;
  updated_at: string;
}

/** Upload form. No version field: the system sets it (1, or latest + 1 for a new version). */
export interface UploadForm {
  doc_id: string; // new document only; a new version keeps the ID of the document it replaces
  title: string;
  doc_type: string;
  effective_date: string;
  status: string;
  owner: string;
  region: string;
  audience: string;
  visibility: "department" | "everyone";
  replaces: string; // document id, or "" for a brand-new document
}

/** Allowed values for uploads, from GET /api/kb/standards/ (same lists the backend checks). */
export interface UploadStandards {
  doc_types: string[];
  regions: string[];
  audiences: string[];
  owners: string[];
  statuses: string[];
  doc_id_pattern: string;
  doc_id_max: number;
  title_max: number;
  name_format: string;
}

/** The standard name an upload would get: uploads/<doc_id>__v<version>.md */
export interface NamePreview {
  doc_id: string;
  version: number | null;
  name: string | null;
  error: string;
}

export type Role = "admin" | "auditor" | "employee";

export interface Me {
  authenticated: boolean;
  username?: string;
  full_name?: string;
  role?: Role;
  department?: string;
  can_manage_users?: boolean; // admins and superusers (for a future "Users" page)
}

/** POST /api/auth/login/ */
export interface LoginResponse {
  access: string;
  refresh: string;
  user: Me;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Citation {
  number: number;
  chunk_id: number;
  doc_path: string;
  doc_title: string;
  section: string;
  text: string;
}

export interface DroppedPassage {
  doc_path: string;
  section: string;
  reason: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  warnings: string[];
  dropped: DroppedPassage[];
  error: string;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

export type ChatStreamEvent =
  | { type: "context"; passages: number; warnings: string[]; dropped: DroppedPassage[] }
  | { type: "delta"; text: string }
  | { type: "error"; message: string }
  | { type: "done"; message: ChatMessage };

export type RunStatus = "queued" | "running" | "done" | "failed";

export interface AuditRun {
  id: number;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  model_id: string;
  prompt_version: string;
  settings: Record<string, unknown>;
  progress: { stage?: string; pairs_done?: number; pairs_total?: number };
  totals: Record<string, number>;
  error: string;
}

export type Severity = "high" | "medium" | "info";
export type FindingStatus = "open" | "confirmed" | "rejected" | "ignored" | "fixed";
export type FeedbackDecision = "real" | "not_conflict" | "ignore";
/** What the auditor clicked: "real" may come with the correct side. */
export interface FeedbackChoice {
  decision: FeedbackDecision;
  winner?: "a" | "b";
}

export interface Finding {
  id: number;
  run: number;
  kind: "contradiction" | "duplicate";
  severity: Severity;
  topic: string;
  chunk_a_id: number;
  chunk_b_id: number;
  doc_a_path: string;
  doc_b_path: string;
  owner_a: string;
  owner_b: string;
  quote_a: string;
  quote_b: string;
  reason: string;
  suggested_truth: "a" | "b" | "";
  resolution_rule: string;
  resolution_reason: string;
  suggested_action: string;
  status: FindingStatus;
  new_side: "a" | "b" | "both" | ""; // new text(s) the chat does not use until an auditor decides
  winner: "a" | "b" | ""; // the side the auditor chose as correct
  created_at: string;
}

export interface ChunkInfo {
  id: number;
  heading_path: string;
  text: string;
  document: KBDocument | null;
}

export interface VerdictInfo {
  id: number;
  stage: string;
  label: string;
  confidence: string;
  reason: string;
  quote_a: string;
  quote_b: string;
  grounded: boolean;
  is_final: boolean;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  cost_usd: number;
  error: string;
  created_at: string;
}

export interface InvestigationStep {
  type: "model" | "tool";
  text?: string;
  tool_requests?: string[];
  name?: string;
  input?: unknown;
  output_preview?: string;
}

export interface InvestigationInfo {
  steps: InvestigationStep[];
  final_label: string;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  error: string;
}

export interface FindingDetail extends Finding {
  chunk_a: ChunkInfo | null;
  chunk_b: ChunkInfo | null;
  verdicts: VerdictInfo[];
  investigation: InvestigationInfo | null;
}

export interface EvalDetail {
  id: string;
  kind: string;
  topic?: string;
  outcome: string;
  note?: string;
  reached_judge?: boolean;
  final_label?: string | null;
  resolver_correct?: boolean;
}

export interface EvalResult {
  id: number;
  run: number;
  created_at: string;
  metrics: Record<string, number | null>;
  details: EvalDetail[];
}
