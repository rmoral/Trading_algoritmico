import { useState } from "react";

import { useKill } from "../api/queries";

interface Props {
  disabled?: boolean;
}

export function KillButton({ disabled }: Props) {
  const kill = useKill();
  const [armed, setArmed] = useState(false);
  const [reason, setReason] = useState("");

  if (!armed) {
    return (
      <button
        type="button"
        className="rounded bg-rose-700 px-4 py-2 font-semibold text-white shadow hover:bg-rose-600 disabled:opacity-50"
        onClick={() => setArmed(true)}
        disabled={disabled}
      >
        Kill switch
      </button>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        kill.mutate(reason || null, { onSettled: () => setArmed(false) });
      }}
      className="flex items-center gap-2"
    >
      <input
        type="text"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="reason (optional)"
        className="rounded bg-slate-800 px-2 py-1 text-sm ring-1 ring-slate-700"
      />
      <button
        type="submit"
        disabled={kill.isPending}
        className="rounded bg-rose-700 px-3 py-1 text-sm font-semibold text-white hover:bg-rose-600 disabled:opacity-50"
      >
        Confirm kill
      </button>
      <button
        type="button"
        onClick={() => setArmed(false)}
        className="rounded bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600"
      >
        Cancel
      </button>
    </form>
  );
}
