import { useState } from "react";
import { QRCodeSVG } from "qrcode.react";

import { ApiError } from "../api/client";
import {
  useMe,
  useTotpDisenroll,
  useTotpEnroll,
  useTotpVerify,
} from "../api/queries";

export function MePage() {
  const me = useMe();

  if (me.isLoading) return <p className="p-6 text-slate-400">Loading…</p>;
  if (!me.data) return <p className="p-6 text-slate-400">No user.</p>;

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <h2 className="mb-3 text-lg font-medium">Account</h2>
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <dt className="text-slate-400">Username</dt>
          <dd>{me.data.username}</dd>
          <dt className="text-slate-400">Last login</dt>
          <dd>{me.data.last_login_at ?? "—"}</dd>
          <dt className="text-slate-400">Status</dt>
          <dd>{me.data.is_active ? "active" : "disabled"}</dd>
        </dl>
      </section>

      <section className="rounded-lg bg-slate-800 p-4 shadow">
        <h2 className="mb-3 text-lg font-medium">Two-factor authentication</h2>
        {me.data.totp_enrolled ? (
          <DisenrollFlow />
        ) : (
          <EnrollFlow />
        )}
      </section>
    </div>
  );
}

function EnrollFlow() {
  const enroll = useTotpEnroll();
  const verify = useTotpVerify();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!enroll.data) {
    return (
      <>
        <p className="mb-3 text-sm text-slate-400">
          TOTP is not enabled. Scan a fresh secret into your authenticator app
          to start.
        </p>
        <button
          type="button"
          onClick={() => enroll.mutate()}
          disabled={enroll.isPending}
          className="rounded bg-emerald-700 px-4 py-2 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          {enroll.isPending ? "Generating…" : "Enable two-factor"}
        </button>
        {enroll.error && (
          <p className="mt-2 text-sm text-rose-400">
            {enroll.error instanceof ApiError
              ? enroll.error.detail
              : "enrollment failed"}
          </p>
        )}
      </>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-400">
        Scan this QR with Google Authenticator, 1Password, or any TOTP app.
        Then enter the 6-digit code to confirm.
      </p>
      <div className="inline-block rounded bg-white p-3">
        <QRCodeSVG value={enroll.data.provisioning_uri} size={180} />
      </div>
      <div>
        <p className="text-xs text-slate-400">Secret (if you can&apos;t scan)</p>
        <code className="block break-all rounded bg-slate-900 px-2 py-1 text-xs">
          {enroll.data.secret}
        </code>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          verify.mutate(
            { secret: enroll.data.secret, code },
            {
              onError: (err) =>
                setError(
                  err instanceof ApiError ? err.detail : "verification failed"
                ),
              onSuccess: () => setCode(""),
            }
          );
        }}
        className="flex items-center gap-2"
      >
        <input
          type="text"
          inputMode="numeric"
          pattern="[0-9]{6}"
          maxLength={6}
          required
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="123456"
          className="w-32 rounded bg-slate-900 px-3 py-2 text-center tracking-widest ring-1 ring-slate-700 focus:ring-emerald-600"
        />
        <button
          type="submit"
          disabled={verify.isPending}
          className="rounded bg-emerald-700 px-4 py-2 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          Verify and enable
        </button>
        {error && <p className="text-sm text-rose-400">{error}</p>}
      </form>
    </div>
  );
}

function DisenrollFlow() {
  const disenroll = useTotpDisenroll();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      <p className="text-sm text-emerald-300">
        Two-factor is enabled. To disable, confirm a current 6-digit code.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          disenroll.mutate(code, {
            onError: (err) =>
              setError(
                err instanceof ApiError ? err.detail : "disable failed"
              ),
            onSuccess: () => setCode(""),
          });
        }}
        className="flex items-center gap-2"
      >
        <input
          type="text"
          inputMode="numeric"
          pattern="[0-9]{6}"
          maxLength={6}
          required
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="123456"
          className="w-32 rounded bg-slate-900 px-3 py-2 text-center tracking-widest ring-1 ring-slate-700 focus:ring-rose-600"
        />
        <button
          type="submit"
          disabled={disenroll.isPending}
          className="rounded bg-rose-700 px-4 py-2 font-medium text-white hover:bg-rose-600 disabled:opacity-50"
        >
          Disable two-factor
        </button>
        {error && <p className="text-sm text-rose-400">{error}</p>}
      </form>
    </div>
  );
}
