const styles: Record<string, string> = {
  high: "bg-red-100 text-red-800",
  medium: "bg-amber-100 text-amber-800",
  info: "bg-sky-100 text-sky-800",
  contradiction: "bg-rose-50 text-rose-700",
  duplicate: "bg-sky-50 text-sky-700",
  open: "bg-slate-100 text-slate-700",
  confirmed: "bg-green-100 text-green-800",
  rejected: "bg-slate-200 text-slate-600",
  ignored: "bg-slate-100 text-slate-500",
  queued: "bg-slate-100 text-slate-700",
  running: "bg-indigo-100 text-indigo-800",
  done: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

export default function Badge({ value }: { value: string }) {
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${styles[value] ?? "bg-slate-100 text-slate-700"}`}>
      {value}
    </span>
  );
}
