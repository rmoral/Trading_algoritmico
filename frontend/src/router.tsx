import { createBrowserRouter, Navigate } from "react-router-dom";

import { AssetPage } from "./pages/AssetPage";
import { ConfigPage } from "./pages/ConfigPage";
import { DashboardPage } from "./pages/DashboardPage";
import { LoginPage } from "./pages/LoginPage";
import { MePage } from "./pages/MePage";
import { ProtectedRoute } from "./pages/ProtectedRoute";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: <ProtectedRoute />,
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },
      { path: "dashboard", element: <DashboardPage /> },
      { path: "asset", element: <AssetPage /> },
      { path: "config", element: <ConfigPage /> },
      { path: "me", element: <MePage /> },
    ],
  },
  { path: "*", element: <Navigate to="/dashboard" replace /> },
]);
