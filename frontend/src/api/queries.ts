// TanStack Query hooks for every endpoint the UI consumes. Keep
// them centralized so a route component never builds an ad-hoc fetch.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  ActiveAssetResponse,
  BotStatusResponse,
  ConfigPolicyResponse,
  TOTPEnrollResponse,
  UserResponse,
} from "./types";

// ---------- Auth ----------

export function useMe() {
  return useQuery<UserResponse>({
    queryKey: ["me"],
    queryFn: () => api<UserResponse>("/api/me"),
    retry: false,
    staleTime: 30_000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      username: string;
      password: string;
      totp_code?: string;
    }) => api<UserResponse>("/api/auth/login", { method: "POST", body }),
    onSuccess: (data) => qc.setQueryData(["me"], data),
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/api/auth/logout", { method: "POST" }),
    onSuccess: () => qc.clear(),
  });
}

// ---------- Status (dashboard poll) ----------

export function useBotStatus() {
  return useQuery<BotStatusResponse>({
    queryKey: ["status"],
    queryFn: () => api<BotStatusResponse>("/api/status"),
    refetchInterval: 3_000,
  });
}

// ---------- Kill switch ----------

export function useKill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reason: string | null) =>
      api<void>("/api/kill", { method: "POST", body: { reason } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["status"] }),
  });
}

// ---------- Config ----------

export function useConfig() {
  return useQuery<ConfigPolicyResponse>({
    queryKey: ["config"],
    queryFn: () => api<ConfigPolicyResponse>("/api/config"),
  });
}

// ---------- Active asset ----------

export function useActiveAsset() {
  return useQuery<ActiveAssetResponse>({
    queryKey: ["active-asset"],
    queryFn: () => api<ActiveAssetResponse>("/api/active-asset"),
    retry: false,
  });
}

export function useSetActiveAsset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) =>
      api<ActiveAssetResponse>("/api/active-asset", {
        method: "PUT",
        body: { symbol },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["active-asset"] }),
  });
}

// ---------- TOTP enrollment ----------

export function useTotpEnroll() {
  return useMutation({
    mutationFn: () =>
      api<TOTPEnrollResponse>("/api/me/totp/enroll", { method: "POST" }),
  });
}

export function useTotpVerify() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { secret: string; code: string }) =>
      api<void>("/api/me/totp/verify", { method: "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}

export function useTotpDisenroll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (code: string) =>
      api<void>("/api/me/totp", { method: "DELETE", body: { code } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}
