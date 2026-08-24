import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Modal, PlatformBadge, ScoreRing, StatusChip, fmtDate, latestCL } from "../components";
import type { BatchStatus, Job, JobList } from "../types";
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
  const [category, setCategory] = useState<"it" | "general" | "">("it");
  const [q, setQ] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [sort, setSort] = useState("updated");
  const [addedFrom, setAddedFrom] = useState("");
  const [addedTo, setAddedTo] = useState("");
  const [addedPreset, setAddedPreset] = useState<"" | "today" | "7d" | "30d">("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const totalPages = Math.max(1, Math.ceil(data.total / pageSize));

  const localIso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

  const applyAddedPreset = (p: "today" | "7d" | "30d") => {
    const now = new Date();
    if (p === "today") {
      setAddedFrom(localIso(now));
      setAddedTo(localIso(now));
    } else {
      const from = new Date(now);
      from.setDate(now.getDate() - (p === "7d" ? 6 : 29));
      setAddedFrom(localIso(from));
      setAddedTo(localIso(now));
    }
    setAddedPreset(p);
  };
  const [stats, setStats] = useState<{
    applied7: number;
    applied30: number;
    total: number;
    weeklyGoal: number;
    appliedThisWeek: number;
  }>({ applied7: 0, applied30: 0, total: 0, weeklyGoal: 15, appliedThisWeek: 0 });
  const [selected, setSelected] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [batch, setBatch] = useState<BatchStatus | null>(null);
  const [showBatchResult, setShowBatchResult] = useState(false);
  const pollRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [jobs, s] = await Promise.all([
        api.listJobs({
          status: status || undefined,
          platform: platform || undefined,
          category: category || undefined,
          q: q || undefined,
          show_all: showAll,
          sort: sort || undefined,
          added_from: addedFrom || undefined,
          added_to: addedTo || undefined,
          limit: pageSize,
          offset: (page - 1) * pageSize,
        }),
        api.stats(),
      ]);
      setData(jobs);
      setStats({
        applied7: s.applied_last_7d,
        applied30: s.applied_last_30d,
        total: s.total,
        weeklyGoal: s.weekly_goal,
        appliedThisWeek: s.applied_this_week,
      });
    } catch (e) {
      pushToast(`載入失敗: ${(e as Error).message}`, "err");
    } finally {
      setLoading(false);
    }
  }, [status, platform, category, q, showAll, sort, addedFrom, addedTo, page, pageSize, pushToast]);

  useEffect(() => {
    load();
  }, [load]);

  // any filter change -> back to page 1
  useEffect(() => {
    setPage(1);
  }, [status, platform, category, q, showAll, sort, addedFrom, addedTo, pageSize]);

  // poll batch progress while running
  useEffect(() => {
    if (batch?.running) {
      const t = window.setInterval(async () => {
        try {
          const s = await api.batchStatus();
          setBatch(s);
          if (!s.running) {
            setShowBatchResult(true);
            load();
          }
        } catch {
          /* ignore */
        }
      }, 2500);
      pollRef.current = t;
      return () => window.clearInterval(t);
    }
    return undefined;
  }, [batch?.running, load]);

  const toggleCheck = (id: number) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const runBatch = async () => {
    if (!checked.size) return;
    try {
      const r = await api.batchApply([...checked]);
      pushToast(r.message, "info");
      setBatch({ running: true, total: r.total, done: 0, results: [] });
      setShowBatchResult(false);
      setChecked(new Set());
    } catch (e) {
      pushToast(`一齊投遞失敗: ${(e as Error).message}`, "err");
    }
  };

  const doBackfill = async () => {
    try {
      const r = await api.backfill();
      pushToast(r.message || "補齊已開始", "info");
      setTimeout(load, 3000);
    } catch (e) {
      pushToast(`補齊失敗: ${(e as Error).message}`, "err");
    }
  };

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
            {stats.appliedThisWeek} <small>/ {stats.weeklyGoal}</small>
          </div>
          <div className="l">
            本週目標
            <div className="goalbar">
              <div
                className={`goalbar-fill ${stats.appliedThisWeek >= stats.weeklyGoal ? "done" : ""}`}
                style={{ width: `${Math.min(100, (stats.appliedThisWeek / stats.weeklyGoal) * 100)}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      <div className="track-tabs">
        <button
          className={`track-tab ${category === "it" ? "active" : ""}`}
          onClick={() => setCategory("it")}
        >
          <b>IT</b> 職位
        </button>
        <button
          className={`track-tab ${category === "general" ? "active" : ""}`}
          onClick={() => setCategory("general")}
        >
          <b>一般</b> 職位（非 IT）
        </button>
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
          <option value="offertoday">OfferToday</option>
          <option value="govhk_gbayes">GovHK · 大灣區計劃</option>
          <option value="govhk_it">GovHK · 資訊及科技界</option>
          <option value="govhk_general">GovHK · 一般職位</option>
        </select>
        <input
          className="search"
          placeholder="搜尋職位 / 公司…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select
          className="chip-btn"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          style={{ appearance: "auto" }}
          title="排序"
        >
          <option value="updated">排序：最近更新</option>
          <option value="created">排序：入庫日期（最新先）</option>
          <option value="posted">排序：刊登日期</option>
          <option value="match">排序：匹配度</option>
        </select>
        <span className="filter-label">入庫日期：</span>
        {(
          [
            ["today", "今日"],
            ["7d", "近 7 日"],
            ["30d", "近 30 日"],
          ] as const
        ).map(([p, label]) => (
          <button
            key={p}
            className={`chip-btn ${addedPreset === p ? "active" : ""}`}
            onClick={() => applyAddedPreset(p)}
          >
            {label}
          </button>
        ))}
        {addedPreset && (
          <button
            className="chip-btn"
            onClick={() => {
              setAddedPreset("");
              setAddedFrom("");
              setAddedTo("");
            }}
            title="清除入庫日期篩選"
          >
            ✕ 清除
          </button>
        )}
        <input
          type="date"
          className="chip-btn"
          value={addedFrom}
          onChange={(e) => {
            setAddedPreset("");
            setAddedFrom(e.target.value);
          }}
          title="入庫日期：由"
          style={{ appearance: "auto" }}
        />
        <input
          type="date"
          className="chip-btn"
          value={addedTo}
          onChange={(e) => {
            setAddedPreset("");
            setAddedTo(e.target.value);
          }}
          title="入庫日期：至"
          style={{ appearance: "auto" }}
        />
        <label className="toggle-low">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          顯示低匹配（{data.hidden_low_match}）
        </label>
        <button className="btn" onClick={doBackfill} title="為最舊嘅未處理職位補上 JD / CL">
          ⇪ 補齊
        </button>
      </div>

      {(checked.size > 0 || batch?.running) && (
        <div className="batch-bar">
          {batch?.running ? (
            <>
              <span className="scan-status" style={{ margin: 0 }}>
                ▣ 一齊投遞中：{batch.done}/{batch.total}
              </span>
              <div className="goalbar" style={{ flex: 1, marginTop: 0 }}>
                <div
                  className="goalbar-fill"
                  style={{ width: `${(batch.done / Math.max(1, batch.total)) * 100}%` }}
                />
              </div>
            </>
          ) : (
            <>
              <span>
                已揀 <b>{checked.size}</b> 份
              </span>
              <button className="btn primary" onClick={runBatch}>
                ▶ 一齊自動投遞
              </button>
              <button className="btn" onClick={() => setChecked(new Set())}>
                清除
              </button>
            </>
          )}
        </div>
      )}

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
            const isChecked = checked.has(job.id);
            return (
              <div
                key={job.id}
                className={`job-card ${isChecked ? "checked" : ""}`}
                onClick={() => openDetail(job)}
              >
                <input
                  type="checkbox"
                  className="select-check"
                  checked={isChecked}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => toggleCheck(job.id)}
                  title="揀選（可以一齊自動投遞）"
                />
                <div className="top">
                  <div>
                    <PlatformBadge platform={job.platform} />
                    {job.dup_count > 0 && (
                      <span className="dupbadge" title="同一職位喺其他平台都有出現">
                        ⚠ 可能重複 ×{job.dup_count}
                      </span>
                    )}
                    <h3 style={{ marginTop: 6 }}>{job.title}</h3>
                    <div className="company">{job.company || "—"}</div>
                  </div>
                  <ScoreRing score={job.match_score} />
                </div>
                <div className="meta">
                  <span>{job.location || "—"}</span>
                  <span>{job.salary_range || "薪酬不詳"}</span>
                  <span title="入庫日期（入咗職位台嘅日子）">入庫 {fmtDate(job.created_at)}</span>
                </div>
                {job.job_summary && (
                  <div className="job-summary" title={job.job_summary}>
                    {job.job_summary}
                  </div>
                )}
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

      {!loading && data.total > 0 && (
        <div className="pager">
          <span className="pager-info">
            第 {page} / {totalPages} 頁 · 共 {data.total} 份
            {data.hidden_low_match > 0 && !showAll && `（低匹配隱藏 ${data.hidden_low_match}）`}
          </span>
          <div className="pager-btns">
            <button className="btn" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
              ‹ 上一頁
            </button>
            <select
              className="chip-btn"
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
              style={{ appearance: "auto" }}
              title="每頁幾多份"
            >
              <option value={20}>20 / 頁</option>
              <option value={50}>50 / 頁</option>
              <option value={100}>100 / 頁</option>
            </select>
            <button
              className="btn"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              下一頁 ›
            </button>
          </div>
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

      {showBatchResult && batch && !batch.running && (
        <Modal onClose={() => setShowBatchResult(false)}>
          <h2 style={{ marginTop: 0 }}>
            一齊投遞結果 <span className="stamp">{batch.results.filter((r) => r.submitted).length}/{batch.total}</span>
          </h2>
          <div className="jd-body" style={{ maxHeight: "50vh" }}>
            {batch.results.map((r) => (
              <div key={r.id} style={{ marginBottom: 10, borderBottom: "1px dashed var(--line)", paddingBottom: 8 }}>
                <div style={{ fontWeight: 600 }}>
                  {r.submitted ? "✔" : r.ok ? "➖" : "✗"} {r.title || `#${r.id}`}
                </div>
                <div style={{ fontSize: 12.5, color: r.submitted ? "var(--teal)" : "var(--ink-soft)" }}>
                  {r.message}
                </div>
              </div>
            ))}
          </div>
          <div className="btnrow" style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={() => setShowBatchResult(false)}>
              關閉
            </button>
            <button className="btn" onClick={() => onOpenHistory()}>
              去投遞檔案睇結果
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
