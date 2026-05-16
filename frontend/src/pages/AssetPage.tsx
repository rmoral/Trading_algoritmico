import { useState } from "react";

import { ApiError } from "../api/client";
import { useActiveAsset, useSetActiveAsset } from "../api/queries";

export function AssetPage() {
  const current = useActiveAsset();
  const set = useSetActiveAsset();
  const [symbol, setSymbol] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <h2 className="mb-3 text-lg font-medium">Active asset</h2>
        {current.isLoading && <p className="text-slate-400">Loading…</p>}
        {current.isError && (
          <p className="text-slate-400">
            No asset selected yet for today.
          </p>
        )}
        {current.data && (
          <div className="text-sm">
            <p className="font-mono text-2xl">{current.data.symbol}</p>
            <p className="mt-1 text-slate-400">
              set by {current.data.set_by} at {current.data.effective_from}
            </p>
          </div>
        )}
      </section>

      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <h2 className="mb-3 text-lg font-medium">Change asset</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            set.mutate(symbol.toUpperCase(), {
              onError: (err) =>
                setError(
                  err instanceof ApiError ? err.detail : "request failed"
                ),
              onSuccess: () => setSymbol(""),
            });
          }}
          className="flex flex-wrap items-center gap-3"
        >
          <input
            type="text"
            placeholder="e.g. AAPL"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            pattern="[A-Za-z][A-Za-z0-9.]{0,7}"
            required
            className="rounded bg-slate-900 px-3 py-2 font-mono uppercase ring-1 ring-slate-700 focus:ring-emerald-600"
          />
          <button
            type="submit"
            disabled={set.isPending}
            className="rounded bg-emerald-700 px-4 py-2 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
          >
            Set
          </button>
          {error && <p className="text-sm text-rose-400">{error}</p>}
        </form>
        <p className="mt-3 text-xs text-slate-500">
          Changing the asset is only allowed when the bot is flat
          (CERRADA). Returns 409 otherwise.
        </p>
      </section>
    </div>
  );
}
