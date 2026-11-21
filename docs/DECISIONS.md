# Design decisions

Why the system works the way it does, what each choice costs, and what I would change at a larger scale.
Each entry: **decision → why → trade-off**.

---

## 1. What the project is for

Company chatbots (RAG) answer from whatever text looks most similar to the question. When the knowledge base holds
an old and a new policy side by side, the bot can answer from either one and sound equally sure. This project:

1. **audits** the knowledge base for contradictions and duplicates and suggests which source is correct,
2. **gates** new uploads, so unchecked text never reaches the chat,
3. makes the **chat obey** the audit: outdated text is hidden, unreviewed conflicts are held, unclear ones are shown
   with a warning.

It never edits documents. Fixing a document is the owner's job; the tool tells them what and where.

---

## 2. Finding conflicts

**Candidate pairs from the vector index (top-k per chunk), not all pairs.**
Why: all pairs grow as n² (10,000 chunks → ~50 million pairs); top-k gives n·k.
Trade-off: a contradiction worded very differently (similarity below the threshold) is never judged.
Measured by `candidate_recall` in the evaluation.

**High similarity is suspicious, not "duplicate".**
Why: "refund within 7 days" and "within 3 days" are almost the same text. Only an exact match (ignoring case and
spaces) is marked duplicate by code; everything else goes to the judge. Pairs whose numbers differ are judged first.

**Only compare passages that at least one person can see together.**
Why: the chat can only mix up two passages if one user can retrieve both. A company-wide document overlaps with
every department.
Trade-off: an Operations-only and a Finance-only document that disagree are not reported, although the company is
inconsistent. At larger scale: audit those pairs too, at low priority.

**The pair limit (`MAX_PAIRS_PER_RUN`) is counted, never silent.**
Each run shows how many pairs were found, cut by the limit, and skipped.

---

## 3. Judging

**LLM judge with JSON labels and quotes checked by code.**
Why: the model must copy the conflicting words exactly from both passages; if it invents a quote, it is asked once
more, then the pair becomes "unsure". This catches made-up evidence cheaply.

**Labels include the traps:** `scope_difference` (India vs UAE), `exception` (rule + exception), `same_meaning`
(3 days vs 72 hours). Precision on traps is measured separately (`trap_false_alarm_rate`).

**An agent only for "unsure" pairs.**
Why: most pairs are decided by one call. Only unclear ones get a LangGraph agent with tools (neighbouring sections,
document details, related passages) and a step limit.

---

## 4. Which side is correct

**Rules first, LLM only as a fallback, "unknown" allowed.**
Order: status (superseded/draft loses) → newer effective date → higher version of the same document → document type
(policy > faq > guide). Rules are free, predictable and explainable.

**The chat hides a passage only when a rule or a human decided.**
Why: if only the LLM chose a winner, both passages stay in the chat with a warning. An LLM mistake must never hide
the correct policy.

**Uploads never win automatically.**
Why: metadata is typed in by the uploader. With "newer date wins", anyone uploading old notes with today's date
would hide the official policy. A new text that conflicts with a live one is **held**; the live one keeps being used.
Two new texts that conflict are both held. An auditor decides.
Trade-off: the auditor must review every conflicting upload. Uploads without conflicts go live without review.

**Replacing a document always needs an auditor's approval**, even without a conflict.
Why: otherwise anyone could mark an unrelated file as "replaces the Refund Policy" and take the policy offline.
Approving a version also decides its open conflicts in its favour (it is now the authoritative text).

---

## 5. Remembering decisions

**Decisions and held texts are tied to text fingerprints (content hashes), not database IDs.**
Why: IDs change when a document is re-loaded, and "side A / side B" follows the IDs. A fingerprint always means
the same words. When a text changes, its fingerprint changes and old decisions stop applying, which is correct:
new words need a new check.

**The newest reviewer decision for a pair is reused by every later audit.**
Why: audits run after every upload. Without this, every run would reopen findings that were already decided.

**Held texts stay held across audits until an auditor decides.**
Why: once the uploaded document is live, a later audit would otherwise see "live vs live" and let the newer date
win, which is exactly what holding prevents.

**Open findings are always re-checked**, outside the top-k and the pair limit.
Why: otherwise a held pair could drop out of the next run and its text would silently become usable.
The verdict comes from the cache, so it costs nothing.

**"Fixed" = the pair was re-checked and is no longer a problem, or one of its texts no longer exists.**
Trade-off: "fixed" is set on the previous run's finding and counted in the new run's totals.

---

## 6. Naming and metadata standard (uploads)

**The system names uploaded documents: `uploads/<doc_id>__v<version>.md`.**
Why: file names typed by people carry no reliable meaning (`refund_policy_FINAL (1).md`), and dates or "final"
end up in names. A stable `doc_id` plus an automatic version makes "which documents are versions of the same
thing" a fact, not a guess. The user can still upload a file with any name.

**`doc_id` is shared by all versions; "new version of" is picked from a list, never typed.**
Why: if people type IDs or forget to mark a replacement, a new version becomes a separate document and conflicts
with the old one. The resolver's version rule compares `doc_id`s (falling back to the title for seeded documents).

**Fixed lists for type, owner, region, audience, served by the backend to the form.**
Why: free text ("CS team", "support", "Customer Support") makes filtering and routing impossible. One list in
`kb/standards.py` is used by both the checks and the form, so they cannot drift apart.

**Region and audience are shown to the judge.**
Why: several false alarms are scope differences (India vs UAE, employees vs delivery partners). Metadata that
states the scope is more reliable than hoping the heading says it. The judge prompt version was bumped (judge-v2)
so cached verdicts for the old passage format are not reused.

**Version numbers are never reused**, even after a rejected or failed version: a name always means one file.

**The existing knowledge base keeps its names.** Trade-off: two naming styles exist side by side. That is realistic
(the legacy knowledge base is messy; that is what the auditor is for), and the answer key refers to those names.

---

## 7. Running the work

**Postgres as the job queue** (`SELECT … FOR UPDATE SKIP LOCKED`) for uploads and audits; one worker process.
Why: no extra infrastructure. Two workers never take the same job.
**At most one audit waits in the queue** (advisory lock + check): a waiting audit has not started, so it will see
every change made before it starts. If one is only running, one more is queued.
At larger scale: Celery/SQS, and audit only around the changed chunks instead of the whole knowledge base.

**Uploads: file first, then the database row; the file is deleted if the row fails.**
Why: the volume never keeps files the database does not know about. Chunks are saved all at once (one transaction),
so a half-processed document never exists.

**Embedding happens in the worker, not in the web request.** One Bedrock call per section can take seconds and can
be throttled; the user sees "processing" instead of a hanging request.

---

## 8. Chat

**Access control in the SQL query** (department filter), so restricted text never reaches the model.
**Only live, non-superseded documents are searched.**
**Passages carry effective date, version and status**, and the prompt says: if passages disagree for the same case,
say so, show both with citations and dates, and say which is newer. This is the safety net for conflicts the audit
has not seen yet (before the first audit, or ones it missed).
**No relevant passage → no LLM call**, the user gets "could not find this".
**Every `[n]` citation is checked by code.**

---

## 9. Known limits (written down on purpose)

- The database grows with every audit run (all pairs and verdicts are stored per run).
- Every audit searches the whole knowledge base (cheap thanks to the cache, but not for huge knowledge bases).
- One Bedrock failure fails the whole run (finished verdicts are cached, so a re-run is cheaper).
- The chat rate limit is per gunicorn process, not shared (fix: Redis, ROADMAP stage 4).
- An auditor's choice hides the losing text for **everyone**, even users who cannot see the winning text
  (e.g. a department-only upload wins over a company-wide policy).
- Markdown only; approvals do not record who approved.
