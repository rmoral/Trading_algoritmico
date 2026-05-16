import type { ReactNode } from "react";

type Tone = "ok" | "warn" | "danger" | "neutral";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "bg-emerald-900 text-emerald-200 ring-emerald-700",
  warn: "bg-amber-900 text-amber-100 ring-amber-700",
  danger: "bg-rose-900 text-rose-100 ring-rose-700",
  neutral: "bg-slate-800 text-slate-200 ring-slate-700",
};

interface Props {
  tone: Tone;
  children: ReactNode;
}

export function Badge({ tone, children }: Props) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}
