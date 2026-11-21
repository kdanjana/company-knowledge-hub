import type { FeedbackChoice } from "../types";

interface Button {
  choice: FeedbackChoice;
  label: string;
  style: string;
  contradictionOnly?: boolean;
}

// "A is correct" / "B is correct" = a real conflict AND which side is right. The decision is remembered
// for the same two texts in later audits, and the chat stops using the other side.
const buttons: Button[] = [
  { choice: { decision: "real", winner: "a" }, label: "✅ A is correct", style: "border-green-300 text-green-800 hover:bg-green-50", contradictionOnly: true },
  { choice: { decision: "real", winner: "b" }, label: "✅ B is correct", style: "border-green-300 text-green-800 hover:bg-green-50", contradictionOnly: true },
  { choice: { decision: "real" }, label: "✅ Real", style: "border-green-300 text-green-800 hover:bg-green-50" },
  { choice: { decision: "not_conflict" }, label: "❌ Not a conflict", style: "border-red-300 text-red-800 hover:bg-red-50" },
  { choice: { decision: "ignore" }, label: "Ignore", style: "border-slate-300 text-slate-700 hover:bg-slate-100" },
];

export default function FeedbackButtons({
  onSend,
  kind,
  disabled,
}: {
  onSend: (choice: FeedbackChoice) => void;
  kind: "contradiction" | "duplicate";
  disabled?: boolean;
}) {
  // For a contradiction the auditor picks a side; a plain "Real" is only offered for duplicates.
  const visible = buttons.filter((b) =>
    kind === "contradiction" ? b.contradictionOnly || b.choice.decision !== "real" : !b.contradictionOnly,
  );
  return (
    <div className="flex flex-wrap gap-2">
      {visible.map((b) => (
        <button
          key={b.label}
          type="button"
          disabled={disabled}
          onClick={() => onSend(b.choice)}
          className={`rounded border px-3 py-1 text-sm disabled:opacity-50 ${b.style}`}
        >
          {b.label}
        </button>
      ))}
    </div>
  );
}
