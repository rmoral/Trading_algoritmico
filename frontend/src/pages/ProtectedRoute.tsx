import { Navigate, Outlet } from "react-router-dom";

import { useMe } from "../api/queries";
import { Header } from "../components/Header";

export function ProtectedRoute() {
  const me = useMe();

  if (me.isLoading) {
    return <p className="p-6 text-slate-400">Loading session…</p>;
  }
  if (me.isError) {
    return <Navigate to="/login" replace />;
  }

  return (
    <>
      <Header />
      <Outlet />
    </>
  );
}
