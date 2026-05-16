import { useEffect, useMemo, useState } from "react";

import { ApiError, type ValidationErrorItem } from "../api/client";
import { useConfig, useUpdateConfig } from "../api/queries";

// Field grouping mirrors CLAUDE.md §7. Top-level fields are flat in
// the payload; sr_strength_weights is the single nested sub-object.
const SECTIONS: { title: string; fields: string[] }[] = [
  {
    title: "Capital and sizing",
    fields: ["account_equity_target_usd", "max_position_size_usd"],
  },
  {
    title: "Per-trade",
    fields: [
      "stop_loss_pct",
      "min_profit_per_trade_usd",
      "max_profit_per_trade_usd",
      "min_r_multiple",
      "max_commission_pct_of_target",
    ],
  },
  {
    title: "S/R thresholds",
    fields: [
      "sr_strong_threshold",
      "sr_weak_threshold",
      "sr_partial_entry_pct",
      "sr_level_tolerance_pct",
      "sr_lookback_minutes",
    ],
  },
  {
    title: "Daily caps",
    fields: [
      "max_daily_loss_usd",
      "max_trades_per_day",
      "max_orders_per_minute",
    ],
  },
  {
    title: "Market microstructure",
    fields: [
      "min_spread_bps",
      "max_spread_bps",
      "earnings_blackout",
      "halt_resume_cooldown_seconds",
    ],
  },
  {
    title: "End-of-day",
    fields: [
      "no_new_entries_before_close_minutes",
      "force_flatten_before_close_minutes",
    ],
  },
  {
    title: "Circuit breaker",
    fields: ["consecutive_losses_limit", "drawdown_pct_from_open"],
  },
  {
    title: "Timing",
    fields: ["entry_limit_cancel_seconds", "config_reload_seconds"],
  },
];

const WEIGHT_FIELDS = [
  "clean_touches",
  "volume_at_price",
  "ma_confluence",
  "persistence",
  "rejection_quality",
] as const;

type FormState = {
  scalars: Record<string, string>;
  weights: Record<string, string>;
  forbiddenTickers: string;
};

function payloadToForm(payload: Record<string, unknown>): FormState {
  const scalars: Record<string, string> = {};
  for (const [k, v] of Object.entries(payload)) {
    if (k === "sr_strength_weights" || k === "forbidden_tickers") continue;
    scalars[k] = String(v);
  }
  const weights: Record<string, string> = {};
  const wRaw =
    (payload["sr_strength_weights"] as Record<string, unknown> | undefined) ??
    {};
  for (const wk of WEIGHT_FIELDS) {
    const raw = wRaw[wk];
    weights[wk] =
      typeof raw === "string" || typeof raw === "number" ? String(raw) : "";
  }
  const forbidden = Array.isArray(payload["forbidden_tickers"])
    ? (payload["forbidden_tickers"] as string[]).join(", ")
    : "";
  return { scalars, weights, forbiddenTickers: forbidden };
}

function formToPayload(form: FormState): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(form.scalars)) {
    if (k === "earnings_blackout") {
      out[k] = v === "true" || v === "True" || v === "1";
    } else {
      out[k] = v;
    }
  }
  out["sr_strength_weights"] = { ...form.weights };
  out["forbidden_tickers"] = form.forbiddenTickers
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  return out;
}

export function ConfigPage() {
  const config = useConfig();
  const update = useUpdateConfig();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<FormState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (config.data && form === null) {
      setForm(payloadToForm(config.data.payload));
    }
  }, [config.data, form]);

  const errorsByField = useMemo(() => fieldErrors, [fieldErrors]);

  if (config.isLoading) return <p className="p-6 text-slate-400">Loading…</p>;
  if (config.isError)
    return (
      <p className="p-6 text-slate-400">
        No active configuration. Run <code>scripts/seed_config.py</code> to
        bootstrap.
      </p>
    );
  if (!form || !config.data)
    return <p className="p-6 text-slate-400">Loading form…</p>;

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    setError(null);
    setFieldErrors({});
    update.mutate(formToPayload(form), {
      onSuccess: () => setEditing(false),
      onError: (err) => {
        if (err instanceof ApiError) {
          setError(err.detail);
          setFieldErrors(buildFieldErrors(err.errors));
        } else {
          setError("update failed");
        }
      },
    });
  }

  function onCancel() {
    if (config.data) setForm(payloadToForm(config.data.payload));
    setError(null);
    setFieldErrors({});
    setEditing(false);
  }

  function setScalar(name: string, value: string) {
    setForm((f) => (f ? { ...f, scalars: { ...f.scalars, [name]: value } } : f));
  }
  function setWeight(name: string, value: string) {
    setForm((f) => (f ? { ...f, weights: { ...f.weights, [name]: value } } : f));
  }

  return (
    <form onSubmit={onSubmit} className="mx-auto max-w-5xl space-y-4 p-6">
      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-medium">Runtime configuration</h2>
          <div className="flex items-center gap-2">
            <p className="text-xs text-slate-400">
              v{config.data.version} · {config.data.created_by} ·{" "}
              {config.data.created_at}
            </p>
            {!editing ? (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="rounded bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600"
              >
                Edit
              </button>
            ) : (
              <>
                <button
                  type="submit"
                  disabled={update.isPending}
                  className="rounded bg-emerald-700 px-3 py-1 text-sm font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
                >
                  {update.isPending ? "Saving…" : "Save new version"}
                </button>
                <button
                  type="button"
                  onClick={onCancel}
                  className="rounded bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600"
                >
                  Cancel
                </button>
              </>
            )}
          </div>
        </div>
        {error && <p className="mt-2 text-sm text-rose-400">{error}</p>}
      </section>

      {SECTIONS.map((sec) => (
        <section key={sec.title} className="rounded-lg bg-slate-800 p-4 shadow">
          <h3 className="mb-3 text-sm font-medium text-slate-300">
            {sec.title}
          </h3>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {sec.fields.map((field) => (
              <Field
                key={field}
                name={field}
                value={form.scalars[field] ?? ""}
                onChange={(v) => setScalar(field, v)}
                error={errorsByField[field]}
                editing={editing}
              />
            ))}
          </div>
        </section>
      ))}

      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <h3 className="mb-3 text-sm font-medium text-slate-300">
          S/R strength weights (must sum to 1.0)
        </h3>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {WEIGHT_FIELDS.map((wf) => (
            <Field
              key={wf}
              name={`sr_strength_weights.${wf}`}
              displayName={wf}
              value={form.weights[wf] ?? ""}
              onChange={(v) => setWeight(wf, v)}
              error={errorsByField[`sr_strength_weights.${wf}`]}
              editing={editing}
            />
          ))}
        </div>
      </section>

      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <h3 className="mb-3 text-sm font-medium text-slate-300">
          Forbidden tickers
        </h3>
        <Field
          name="forbidden_tickers"
          value={form.forbiddenTickers}
          onChange={(v) =>
            setForm((f) => (f ? { ...f, forbiddenTickers: v } : f))
          }
          error={errorsByField["forbidden_tickers"]}
          editing={editing}
          placeholder="comma-separated, e.g. SOFI, AMC"
        />
      </section>
    </form>
  );
}

function Field({
  name,
  displayName,
  value,
  onChange,
  error,
  editing,
  placeholder,
}: {
  name: string;
  displayName?: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  editing: boolean;
  placeholder?: string;
}) {
  return (
    <label className="block text-sm">
      <span className="block font-mono text-xs text-slate-400">
        {displayName ?? name}
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        readOnly={!editing}
        placeholder={placeholder}
        className={`mt-1 block w-full rounded bg-slate-900 px-3 py-2 font-mono ring-1 ring-slate-700 focus:ring-emerald-600 ${
          editing ? "" : "opacity-70"
        }`}
      />
      {error && <p className="mt-1 text-xs text-rose-400">{error}</p>}
    </label>
  );
}

function buildFieldErrors(
  errors: ValidationErrorItem[] | undefined
): Record<string, string> {
  if (!errors) return {};
  const map: Record<string, string> = {};
  for (const err of errors) {
    // loc looks like ['body', 'max_daily_loss_usd'] or
    // ['body', 'sr_strength_weights', 'clean_touches'].
    const key = err.loc.slice(1).join(".");
    map[key] = err.msg;
  }
  return map;
}
