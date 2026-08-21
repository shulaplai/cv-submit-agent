import type { BatchStatus, EmailPreview, EmailTemplate, Job, JobList, Profile, ScanStatus, Stats } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {};
  if (!(init?.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(url, { ...init, headers });
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
  apply: (id: number, auto?: boolean, template?: string) =>
    req<{ ok: boolean; kind: string; submitted?: boolean; url?: string; to?: string; message: string; preview?: unknown }>(
      `/api/jobs/${id}/apply`,
      { method: "POST", body: JSON.stringify({ auto, template }) }
    ),
  markApplied: (id: number) => req<Job>(`/api/jobs/${id}/mark-applied`, { method: "POST" }),
  updateJob: (id: number, patch: Record<string, unknown>) =>
    req<Job>(`/api/jobs/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  scanStatus: () => req<ScanStatus>("/api/scan/status"),
  startScan: () => req<{ started: boolean; message: string }>("/api/scan", { method: "POST" }),
  stopScan: () => req<{ stopped: boolean; message: string }>("/api/scan/stop", { method: "POST" }),
  backfill: () => req<{ started: boolean; message: string }>("/api/scan/backfill", { method: "POST" }),
  stats: () => req<Stats>("/api/stats"),
  profile: () => req<Profile>("/api/profile"),
  saveProfile: (p: Record<string, unknown>) =>
    req<Profile>("/api/profile", { method: "PUT", body: JSON.stringify(p) }),
  testLLM: () => req<{ ok: boolean; latency_ms: number; model: string; error: string }>(
    "/api/profile/test-llm",
    { method: "POST" }
  ),
  extractSkills: () => req<{ skills: string[] }>("/api/profile/extract-skills", { method: "POST" }),
  generateIntro: (lang: "zh" | "en") => {
    const fd = new FormData();
    fd.append("lang", lang);
    return req<{ lang: string; text: string }>("/api/profile/generate-intro", { method: "POST", body: fd });
  },
  generateAfterCvIntro: (lang: "zh" | "en", topic: "it" | "general") => {
    const fd = new FormData();
    fd.append("lang", lang);
    fd.append("topic", topic);
    return req<{ lang: string; topic: string; text: string }>("/api/profile/generate-after-cv-intro", { method: "POST", body: fd });
  },
  emailPreview: (id: number, template?: string) =>
    req<EmailPreview>(`/api/jobs/${id}/email-preview${template ? `?template=${encodeURIComponent(template)}` : ""}`),
  emailTemplates: () => req<{ templates: EmailTemplate[] }>("/api/jobs/email-templates"),
  uploadCV: (kind: "en" | "zh", file: File) => {
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", file);
    return req<Profile>("/api/profile/cv", { method: "POST", body: fd });
  },
  batchApply: (ids: number[], auto?: boolean) =>
    req<{ started: boolean; total: number; message: string }>("/api/jobs/batch-apply", {
      method: "POST",
      body: JSON.stringify({ ids, auto }),
    }),
  batchStatus: () => req<BatchStatus>("/api/jobs/batch-status"),
  browserStatus: () => req<{ using_real_chrome: boolean; chrome_running: boolean; cdp_url: string; note: string }>("/api/browser/status"),
  launchChrome: () => req<{ ok: boolean; restart_needed?: boolean; message: string }>("/api/browser/launch-chrome", { method: "POST" }),
  restartChrome: () => req<{ ok: boolean; restart_needed?: boolean; message: string }>("/api/browser/restart-chrome", { method: "POST" }),
};
