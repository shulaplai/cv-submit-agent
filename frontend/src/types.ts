export type Status =
  | "pending_review"
  | "low_match"
  | "applied"
  | "needs_manual_intervention"
  | "failed"
  | "interviewing"
  | "rejected"
  | "offer";

export interface CoverLetter {
  id: number;
  application_id: number;
  language: string;
  content: string;
  version: number;
  created_at: string;
}

export interface Job {
  id: number;
  platform: string;
  job_id_on_platform: string;
  category: "it" | "general";
  url: string;
  external_url: string;
  title: string;
  company: string;
  location: string;
  salary_range: string;
  jd_text: string;
  jd_language: string;
  posted_at: string;
  scraped_at: string;
  match_score: number;
  match_reason: string;
  job_summary: string;
  apply_method: string;
  contact_email: string;
  contact_person: string;
  status: Status;
  applied_at: string | null;
  interview_stage: string;
  notes: string;
  dup_key: string;
  dup_count: number;
  created_at: string;
  updated_at: string;
  cover_letters: CoverLetter[];
}

export interface JobList {
  items: Job[];
  total: number;
  hidden_low_match: number;
  // filter-chip badge counts (respecting the active filters, minus the counted dimension)
  facets: { statuses: Record<string, number>; platforms: Record<string, number> };
}

export interface ScanStatus {
  running: boolean;
  last: {
    at: string;
    scanned: number;
    new_jobs: number;
    skipped_duplicates: number;
    skipped_old: number;
    capped: number;
    enriched: number;
    backfilled: number;
    low_match: number;
    details_fetched: number;
    stopped: boolean;
    errors: string[];
    track: string;
    tracks: Record<string, { scanned: number; new_jobs: number; skipped_old: number; capped: number }>;
  } | null;
  last_backfill: { at: string; processed: number } | null;
  progress: { platform: string; phase: string; count: number };
  last_error: string | null;
  stop_requested: boolean;
  track: string | null;
}

export interface Stats {
  total: number;
  by_status: Record<string, number>;
  by_platform: Record<string, number>;
  applied_last_7d: number;
  applied_last_30d: number;
  weekly_applied: { week: string; count: number }[];
  weekly_goal: number;
  applied_this_week: number;
}

export interface Profile {
  name: string;
  email: string;
  cv_en_path: string;
  cv_zh_path: string;
  skills_json: string;
  gba_age_under_29: boolean;
  gba_edu_associate_degree: boolean;
  llm_api_key: string;
  llm_fallback_api_key: string;
  auto_submit: boolean;
  intro_en: string;
  intro_zh: string;
  offertoday_cv_en_keyword: string;
  offertoday_cv_zh_keyword: string;
  after_cv_intro_it_zh: string;
  after_cv_intro_it_en: string;
  after_cv_intro_general_zh: string;
  after_cv_intro_general_en: string;
  it_keywords: string;
  it_track_enabled: boolean;
  general_track_enabled: boolean;
  general_job_keywords: string;
  offertoday_general_search_terms: string;
  govhk_it_max_jobs: number;
  govhk_general_max_jobs: number;
  offertoday_it_max_per_search: number;
  offertoday_general_max_per_search: number;
  updated_at: string;
}

export interface BatchResultItem {
  id: number;
  title: string;
  ok: boolean;
  submitted: boolean;
  message: string;
}

export interface BatchStatus {
  running: boolean;
  total: number;
  done: number;
  results: BatchResultItem[];
}

export interface EmailPreview {
  to: string;
  contact_person: string;
  subject: string;
  body: string;
  attachment: string;
}

export interface EmailTemplate {
  key: string;
  label_zh: string;
  label_en: string;
  desc: string;
}

export const STATUS_LABEL: Record<Status, string> = {
  pending_review: "待處理",
  low_match: "低匹配",
  applied: "已投遞",
  needs_manual_intervention: "需介入",
  failed: "失敗",
  interviewing: "面試中",
  rejected: "已拒絕",
  offer: "錄取",
};

export const PLATFORM_LABEL: Record<string, string> = {
  jobsdb: "JobsDB",
  offertoday: "OfferToday",
  govhk_gbayes: "GovHK · 大灣區計劃",
  govhk_it: "GovHK · 資訊及科技界",
  govhk_general: "GovHK · 一般職位",
  govhk: "GovHK",
};

export const CATEGORY_LABEL: Record<string, string> = {
  it: "IT 職位",
  general: "一般職位",
};
