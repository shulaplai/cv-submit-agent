import type { Job, JobList, Profile, ScanStatus, Stats } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listJobs: (params: Record<string, string | boolean | number | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "" && v !== false) q.set(k, String(v));
    }
    return req<JobList>(`/api/jobs?${q.toString()}`);
  },
  getJob: (id: number) => req<Job>(`/api/jobs/${id}`),
  refreshJob: (id: number) => req<Job>(`/api/jobs/${id}/refresh`, { method: "POST" }),
  saveCoverLetter: (id: number, content: string) =>
    req<Job>(`/api/jobs/${id}/cover-letters`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  regenerateCL: (id: number, instructions: string) =>
    req<Job>(`/api/jobs/${id}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ instructions }),
    }),
  apply: (id: number) =>
    req<{ ok: boolean; kind: string; url?: string; to?: string; message: string; preview?: unknown }>(
      `/api/jobs/${id}/apply`,
      { method: "POST" }
    ),
  markApplied: (id: number) => req<Job>(`/api/jobs/${id}/mark-applied`, { method: "POST" }),
  updateJob: (id: number, patch: Record<string, unknown>) =>
    req<Job>(`/api/jobs/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  scanStatus: () => req<ScanStatus>("/api/scan/status"),
  startScan: () => req<{ started: boolean; message: string }>("/api/scan", { method: "POST" }),
  stats: () => req<Stats>("/api/stats"),
  profile: () => req<Profile>("/api/profile"),
  saveProfile: (p: Record<string, unknown>) =>
    req<Profile>("/api/profile", { method: "PUT", body: JSON.stringify(p) }),
};
