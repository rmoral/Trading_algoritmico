import { NavLink, useNavigate } from "react-router-dom";

import { useLogout, useMe } from "../api/queries";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/asset", label: "Asset" },
  { to: "/config", label: "Config" },
];

export function Header() {
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();

  return (
    <header className="border-b border-slate-800 bg-slate-950">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-6">
          <span className="font-semibold tracking-tight">Trading bot</span>
          <nav className="flex gap-4 text-sm">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  isActive
                    ? "text-emerald-300"
                    : "text-slate-400 hover:text-slate-200"
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm">
          {me.data && (
            <span className="text-slate-400">{me.data.username}</span>
          )}
          <button
            type="button"
            onClick={() =>
              logout.mutate(undefined, {
                onSuccess: () => navigate("/login", { replace: true }),
              })
            }
            className="rounded bg-slate-800 px-3 py-1 hover:bg-slate-700"
          >
            Logout
          </button>
        </div>
      </div>
    </header>
  );
}
