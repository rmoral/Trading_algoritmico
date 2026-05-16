import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useLogin } from "../api/queries";

export function LoginPage() {
  const navigate = useNavigate();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [needsTotp, setNeedsTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    login.mutate(
      {
        username,
        password,
        totp_code: needsTotp ? totp : undefined,
      },
      {
        onSuccess: () => navigate("/dashboard", { replace: true }),
        onError: (err) => {
          if (err instanceof ApiError && err.detail === "totp_required") {
            setNeedsTotp(true);
            setError("Enter your 6-digit code.");
            return;
          }
          setError(err instanceof ApiError ? err.detail : "login failed");
        },
      }
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-900">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-lg bg-slate-800 p-6 shadow"
      >
        <h1 className="text-xl font-semibold">Sign in</h1>

        <label className="block">
          <span className="text-sm text-slate-300">Username</span>
          <input
            type="text"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            className="mt-1 block w-full rounded bg-slate-900 px-3 py-2 ring-1 ring-slate-700 focus:ring-emerald-600"
          />
        </label>

        <label className="block">
          <span className="text-sm text-slate-300">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="mt-1 block w-full rounded bg-slate-900 px-3 py-2 ring-1 ring-slate-700 focus:ring-emerald-600"
          />
        </label>

        {needsTotp && (
          <label className="block">
            <span className="text-sm text-slate-300">
              Two-factor code (6 digits)
            </span>
            <input
              type="text"
              inputMode="numeric"
              pattern="[0-9]{6}"
              maxLength={6}
              autoComplete="one-time-code"
              value={totp}
              onChange={(e) => setTotp(e.target.value)}
              required
              className="mt-1 block w-full rounded bg-slate-900 px-3 py-2 tracking-widest ring-1 ring-slate-700 focus:ring-emerald-600"
            />
          </label>
        )}

        {error && <p className="text-sm text-rose-400">{error}</p>}

        <button
          type="submit"
          disabled={login.isPending}
          className="w-full rounded bg-emerald-700 px-3 py-2 font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
        >
          {login.isPending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
