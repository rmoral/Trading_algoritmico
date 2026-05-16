import { useConfig } from "../api/queries";

export function ConfigPage() {
  const config = useConfig();

  if (config.isLoading) return <p className="p-6 text-slate-400">Loading…</p>;
  if (config.isError)
    return (
      <p className="p-6 text-slate-400">
        No active configuration. Run <code>scripts/seed_config.py</code> to
        bootstrap.
      </p>
    );

  const c = config.data!;
  const entries = Object.entries(c.payload).sort(([a], [b]) => a.localeCompare(b));

  return (
    <div className="mx-auto max-w-5xl space-y-4 p-6">
      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium">Runtime configuration</h2>
          <p className="text-xs text-slate-400">
            v{c.version} · set by {c.created_by} at {c.created_at}
          </p>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Read-only in this build. Editing UI lands in a follow-up commit;
          the API already supports PUT.
        </p>
      </section>

      <section className="overflow-hidden rounded-lg bg-slate-800 shadow">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-left text-xs uppercase text-slate-400">
            <tr>
              <th className="px-4 py-2">Key</th>
              <th className="px-4 py-2">Value</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([k, v]) => (
              <tr key={k} className="border-t border-slate-700">
                <td className="px-4 py-2 font-mono text-slate-300">{k}</td>
                <td className="px-4 py-2 font-mono text-slate-100">
                  {formatValue(v)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean" || typeof v === "bigint")
    return v.toString();
  return JSON.stringify(v);
}
