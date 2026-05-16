import { Badge } from "../components/Badge";
import { KillButton } from "../components/KillButton";
import { useBotStatus } from "../api/queries";
import type { ConnectionState } from "../api/types";

function connectionTone(state: ConnectionState) {
  if (state === "connected") return "ok" as const;
  if (state === "disconnected") return "danger" as const;
  return "warn" as const;
}

export function DashboardPage() {
  const status = useBotStatus();

  if (status.isLoading) return <p className="p-6 text-slate-400">Loading…</p>;
  if (status.isError)
    return (
      <p className="p-6 text-rose-400">
        Failed to load status: {String(status.error)}
      </p>
    );

  const s = status.data!;
  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Card title="Connection">
          <Badge tone={connectionTone(s.connection_state)}>
            {s.connection_state}
          </Badge>
        </Card>

        <Card title="Kill switch">
          <Badge tone={s.kill_switch_tripped ? "danger" : "ok"}>
            {s.kill_switch_tripped ? "TRIPPED" : "idle"}
          </Badge>
          {s.kill_switch_reason && (
            <p className="mt-2 text-xs text-slate-400">
              {s.kill_switch_reason}
            </p>
          )}
        </Card>

        <Card title="Today P&L">
          {s.today_pnl ? (
            <div>
              <p className="text-2xl font-semibold">
                {s.today_pnl.net_pnl}
                <span className="ml-2 text-sm text-slate-400">USD net</span>
              </p>
              <p className="text-xs text-slate-400">
                {s.today_pnl.n_trades} trades · W {s.today_pnl.n_wins} / L{" "}
                {s.today_pnl.n_losses}
              </p>
            </div>
          ) : (
            <p className="text-slate-400">No P&L yet</p>
          )}
        </Card>
      </section>

      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-medium">Open position</h2>
          <KillButton disabled={s.kill_switch_tripped} />
        </div>
        {s.open_position ? (
          <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-5">
            <Field label="Symbol" value={s.open_position.symbol} />
            <Field label="Side" value={s.open_position.side} />
            <Field label="Qty" value={s.open_position.qty} />
            <Field
              label="Avg entry"
              value={s.open_position.avg_entry_price}
            />
            <Field label="State" value={s.open_position.state} />
          </dl>
        ) : (
          <p className="text-slate-400">Flat. Discovery strategy is running.</p>
        )}
      </section>
    </div>
  );
}

function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg bg-slate-800 p-4 shadow">
      <h3 className="mb-2 text-sm text-slate-400">{title}</h3>
      {children}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="font-mono">{value}</dd>
    </div>
  );
}
