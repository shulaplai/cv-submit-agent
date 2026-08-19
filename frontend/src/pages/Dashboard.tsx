import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { PlatformBadge, ScoreRing, StatusChip, fmtDate, latestCL } from "../components";
import type { Job, JobList } from "../types";
import { JobDetail } from "./JobDetail";

export function Dashboard({
  pushToast,
  onOpenHistory,
}: {
  pushToast: (text: string, kind?: "info" | "ok" | "err") => void;
  onOpenHistory: () => void;
}) {
  const [data, setData] = useState<JobList>({ items: [], total: 0, hidden_low_match: 0 });
  const [status, setStatus] = useState("");
  const [platform, setPlatform] = useState("");
  const [q, setQ] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [stats, setStats] = useState<{ applied7: number; applied30: number; total: number }>({
    applied7: 0,
    applied30: 0,
    total: 0,
  });
  const [selected, setSelected] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [jobs, s] = await Promise.all([
        api.listJobs({ status: status || undefined, platform: platform || undefined, q: q || undefined, show_all: showAll }),
        api.stats(),
      ]);
      setData(jobs);
      setStats({
        applied7: s.applied_last_7d,
        applied30: s.applied_last_30d,
        total: s.total,
      });
    } catch (e) {
      pushToast(`載入失敗: ${(e as Error).message}`, "err");
    } finally {
      setLoading(false);
    }
  }, [status, platform, q, showAll, pushToast]);

  useEffect(() => {
    load();
  }, [load]);

  const openDetail = async (job: Job) => {
    try {
      const fresh = await api.getJob(job.id);
      setSelected(fresh);
    } catch (e) {
      pushToast(`載入職位失敗: ${(e as Error).message}`, "err");
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="kicker">Dashboard · 職位台</div>
          <h1>
            搵工<span className="stamp">運作台</span>
          </h1>
        </div>
      </div>

      <div className="stat-strip">
        <div className="stat">
          <div className="n">{stats.total}</div>
          <div className="l">全部職位</div>
        </div>
        <div className="stat accent">
          <div className="n">{stats.applied7}</div>
          <div className="l">7 日內已投遞</div>
        </div>
        <div className="stat teal">
          <div className="n">{stats.applied30}</div>
          <div className="l">30 日內已投遞</div>
        </div>
        <div className="stat">
          <div className="n">
            {data.total} <small>份</small>
          </div>
          <div className="l">顯示中</div>
        </div>
      </div>

      <div className="filterbar">
        {["", "pending_review", "applied", "interviewing", "needs_manual_intervention", "failed"].map((s) => (
          <button
            key={s || "all"}
            className={`chip-btn ${status === s ? "active" : ""}`}
            onClick={() => setStatus(s)}
          >
            {s === "" ? "全部" : s === "pending_review" ? "待處理" : s === "applied" ? "已投遞" : s === "interviewing" ? "面試中" : s === "needs_manual_intervention" ? "需介入" : "失敗"}
          </button>
        ))}
        <select
          className="chip-btn"
          value={platform}
          onChange={(e) => setPlatform(e.target.value)}
          style={{ appearance: "auto" }}
        >
          <option value="">所有平台</option>
          <option value="jobsdb">JobsDB</option>
          <option value="offertoday">OfferToday</option>
          <option value="govhk">GovHK</option>
        </select>
        <input
          className="search"
          placeholder="搜尋職位 / 公司…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <label className="toggle-low">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          顯示低匹配（{data.hidden_low_match}）
        </label>
      </div>

      {loading ? (
        <div className="empty">載入中…</div>
      ) : data.items.length === 0 ? (
        <div className="empty">
          未有職位 — 撳左邊「立即掃描」開始；之後可以喺{" "}
          <a onClick={onOpenHistory} style={{ textDecoration: "underline", cursor: "pointer" }}>
            投遞檔案
          </a>{" "}
          追蹤進度。
        </div>
      ) : (
        <div className="job-grid">
          {data.items.map((job) => {
            const cl = latestCL(job);
            return (
              <div key={job.id} className="job-card" onClick={() => openDetail(job)}>
                <div className="top">
                  <div>
                    <PlatformBadge platform={job.platform} />
                    <h3 style={{ marginTop: 6 }}>{job.title}</h3>
                    <div className="company">{job.company || "—"}</div>
                  </div>
                  <ScoreRing score={job.match_score} />
                </div>
                <div className="meta">
                  <span>{job.location || "—"}</span>
                  <span>{job.salary_range || "薪酬不詳"}</span>
                </div>
                <div className="foot">
                  <StatusChip status={job.status} />
                  <span className={`cl-ready ${cl ? "" : "no"}`}>
                    {job.apply_method === "email"
                      ? cl
                        ? "✉ CL 已備 · Email 申請"
                        : "✉ 待生成 CL"
                      : cl
                        ? "✔ CL 已備"
                        : "✎ 未生成 CL"}
                  </span>
                </div>
                {job.applied_at && (
                  <div className="meta" style={{ color: "var(--teal)" }}>
                    已投：{fmtDate(job.applied_at)}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {selected && (
        <JobDetail
          job={selected}
          onClose={() => setSelected(null)}
          onChanged={async (fresh) => {
            setSelected(fresh);
            await load();
          }}
          pushToast={pushToast}
        />
      )}
    </>
  );
}
