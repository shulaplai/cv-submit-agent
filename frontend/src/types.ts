export type Status =
  | "pending_review"
  | "low_match"
  | "applied"
  | "needs_manual_intervention"
  | "failed"
  | "interviewing"
  | "rejected"
  | "offer"
  | "no_response";

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
  location_uncertain?: boolean;
  salary_range: string;
  jd_text: string;
  jd_language: string;
  posted_at: string;
  scraped_at: string;
  match_score: number;
  match_reason: string;
  job_summary: string;
  // fit 標籤（職位台篩選 chip）
  ai_match: boolean;
  // AI 相關度："" | "title"（標題有 AI／agent）| "jd"（只喺 JD 提到）
  ai_strength: string;
  // 邊個搜尋字詞／渠道帶入呢份工
  source_query: string;
  // 批量 AI 檢查結果
  ai_verdict: "" | "it_ai" | "it" | "non_it";
  ai_verdict_reason: string;
  is_contract: boolean;
  is_agency: boolean;
  match_level: "" | "under" | "fit" | "over";
  // 申請時實際會交邊份 CV（AI 版／Full-stack 版／Developer 版／通用版）
  cv_variant: string;
  // 發送前 AI 潤色成品
  offertoday_intro_polished: string;
  email_polished_at: string | null;
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
  facets: {
    statuses: Record<string, number>;
    platforms: Record<string, number>;
    ai?: number;
    contract?: number;
    agency?: number;
    levels?: Record<string, number>;
  };
}

export interface ScanStatus {
  running: boolean;
  last: {
    at: string;
    scanned: number;
    new_jobs: number;
    skipped_duplicates: number;
    skipped_old: number;
    skipped_location?: number;
    location_uncertain?: number;
    capped: number;
    enriched: number;
    backfilled: number;
    low_match: number;
    details_fetched: number;
    priority_kept?: number;
    priority_capped?: number;
    skipped_blocked?: number;
    ai_checked?: number;
    ai_non_it?: number;
    ai_llm_calls?: number;
    stopped: boolean;
    errors: string[];
    track: string;
    channels?: string[];
    tracks: Record<string, {
      scanned: number; new_jobs: number; skipped_old: number;
      skipped_location?: number; capped: number;
    }>;
  } | null;
  last_backfill: {
    at: string; processed: number; scope?: string; left?: number;
  } | null;
  last_jd_backfill?: { at: string; processed: number; left: number } | null;
  progress: { platform: string; phase: string; count: number };
  last_error: string | null;
  stop_requested: boolean;
  track: string | null;
  channels?: string[];
  channel_labels?: Record<string, string>;
}

export interface FunnelRow {
  key: string;
  label: string;
  applied: number;
  responded: number;
  interviewing: number;
  rejected: number;
  no_response: number;
  offer: number;
  pending: number;
  response_rate: number;
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
  funnel?: FunnelRow[];
  funnel_by_category?: FunnelRow[];
  funnel_by_ai?: FunnelRow[];
  funnel_by_score?: FunnelRow[];
}

export interface Profile {
  name: string;
  email: string;
  cv_en_path: string;
  cv_zh_path: string;
  cv_ai_en_path: string;
  cv_ai_zh_path: string;
  cv_fullstack_en_path: string;
  cv_fullstack_zh_path: string;
  cv_developer_en_path: string;
  cv_developer_zh_path: string;
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
  after_cv_intro_ai_zh: string;
  after_cv_intro_ai_en: string;
  cv_ai_title_keywords: string;
  it_keywords: string;
  it_track_enabled: boolean;
  general_track_enabled: boolean;
  general_job_keywords: string;
  non_it_keywords: string;
  general_wanted_locations: string;
  offertoday_cv_ai_keyword: string;
  offertoday_cv_it_keyword: string;
  offertoday_cv_general_zh_keyword: string;
  offertoday_cv_general_en_keyword: string;
  offertoday_general_search_terms: string;
  offertoday_it_search_terms: string;
  govhk_it_max_jobs: number;
  govhk_general_max_jobs: number;
  offertoday_it_max_per_search: number;
  offertoday_general_max_per_search: number;
  // 掃描量
  offertoday_it_max_searches: number;
  offertoday_general_max_searches: number;
  max_scan_jobs: number;
  // 高分豁免上限
  cap_bypass_enabled: boolean;
  cap_bypass_min_score: number;
  priority_keywords: string;
  priority_extra_max: number;
  // LLM 預算
  max_enrich_per_scan: number;
  enrich_all_it: boolean;
  max_enrich_it_per_scan: number;
  enrich_general_jobs: boolean;
  // AI 搜尋組
  ai_search_terms: string;
  ai_search_max_searches: number;
  ai_search_max_age_days: number;
  ai_stale_action: string;
  // 批量 AI 檢查設定
  ai_check_enabled: boolean;
  ai_check_batch_size: number;
  ai_check_limit: number;
  ai_check_after_scan: boolean;
  // 保險／地產封鎖字眼（留空 = 內建）
  it_blocked_keywords: string;
  // SMTP 自動寄信
  send_method: string;
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_password: string;
  smtp_from_name: string;
  smtp_from_email: string;
  smtp_use_ssl: boolean;
  smtp_bcc_self: boolean;
  // 掃描節奏
  scan_job_delay_min_seconds: number;
  scan_job_delay_max_seconds: number;
  scan_hour: number;
  scan_day_interval: number;
  // 求職者資歷
  years_experience: number;
  prefer_ai: boolean;
  avoid_contract: boolean;
  avoid_agency: boolean;
  // 發送前 AI 潤色
  email_polish_enabled: boolean;
  email_polish_instructions: string;
  intro_polish_enabled: boolean;
  intro_polish_instructions: string;
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
  polished?: boolean;
  body_original?: string;
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
  no_response: "冇回音",
};

export const LEVEL_LABEL: Record<string, string> = {
  under: "資歷有餘",
  fit: "資歷啱",
  over: "資歷超出",
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
