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
  apply_method: string;
  contact_email: string;
  contact_person: string;
  status: Status;
  applied_at: string | null;
  interview_stage: string;
  notes: string;
  created_at: string;
  updated_at: string;
  cover_letters: CoverLetter[];
}

export interface JobList {
  items: Job[];
  total: number;
  hidden_low_match: number;
}

export interface ScanStatus {
  running: boolean;
  last: {
    at: string;
    scanned: number;
    new_jobs: number;
    skipped_duplicates: number;
    enriched: number;
    low_match: number;
    errors: string[];
  } | null;
  last_error: string | null;
}

export interface Stats {
  total: number;
  by_status: Record<string, number>;
  by_platform: Record<string, number>;
  applied_last_7d: number;
  applied_last_30d: number;
  weekly_applied: { week: string; count: number }[];
}

export interface Profile {
  name: string;
  email: string;
  cv_en_path: string;
  cv_zh_path: string;
  skills_json: string;
  gba_age_under_29: boolean;
  gba_edu_associate_degree: boolean;
  updated_at: string;
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
  govhk: "GovHK",
};
