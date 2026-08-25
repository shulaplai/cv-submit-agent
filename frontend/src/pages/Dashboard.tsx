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
  const [data, setData] = useState<JobList>({ items: [], total: 0, hidden_low_match: 0, facets: { statuses: {}, platforms: {} } });
  const [statuses, setStatuses] = useState<string[]>([]);   // 複選
  const [platforms, setPlatforms] = useState<string[]>([]); // 複選
  const [readyOnly, setReadyOnly] = useState(false);        // ⚡ 可以即刻投遞
  const [category, setCategory] = useState<"it" | "general" | "">("it");
  const [q, setQ] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [sort, setSort] = useState("updated");
  const [addedFrom, setAddedFrom] = useState("");
  const [addedTo, setAddedTo] = useState("");
  const [addedPreset, setAddedPreset] = useState<"" | "today" | "7d" | "30d">("");
  const [postedFrom, setPostedFrom] = useState("");
  const [postedTo, setPostedTo] = useState("");
  const [minMatch, setMinMatch] = useState("");
  const [maxMatch, setMaxMatch] = useState("");
  const [hasJd, setHasJd] = useState(false);
  const [hasCl, setHasCl] = useState(false);
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

  const toggleStatus = (s: string) =>
    setStatuses((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));
  const togglePlatform = (p: string) =>
    setPlatforms((prev) => (prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]));

  const clearFilters = () => {
    setStatuses([]);
    setPlatforms([]);
    setReadyOnly(false);
    setQ("");
    setShowAll(false);
    setSort("updated");
    setAddedFrom("");
    setAddedTo("");
    setAddedPreset("");
    setPostedFrom("");
    setPostedTo("");
    setMinMatch("");
    setMaxMatch("");
    setHasJd(false);
    setHasCl(false);
  };

  const load = useCallback(async () => {
    try {
      const [jobs, s] = await Promise.all([
        api.listJobs({
          status: statuses.length ? statuses.join(",") : undefined,
          platform: platforms.length ? platforms.join(",") : undefined,
          category: category || undefined,
          q: q || undefined,
          show_all: showAll,
          sort: sort || undefined,
          added_from: addedFrom || undefined,
          added_to: addedTo || undefined,
          posted_from: postedFrom || undefined,
          posted_to: postedTo || undefined,
          min_match: minMatch === "" ? undefined : Number(minMatch),
          max_match: maxMatch === "" ? undefined : Number(maxMatch),
          has_jd: hasJd || undefined,
          has_cl: hasCl || undefined,
          ready_to_apply: readyOnly || undefined,
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
  }, [statuses, platforms, category, q, showAll, sort, addedFrom, addedTo,
      postedFrom, postedTo, minMatch, maxMatch, hasJd, hasCl, readyOnly,
      page, pageSize, pushToast]);

  useEffect(() => {
    load();
  }, [load]);

  // any filter change -> back to page 1
  useEffect(() => {
    setPage(1);
  }, [statuses, platforms, category, q, showAll, sort, addedFrom, addedTo,
      postedFrom, postedTo, minMatch, maxMatch, hasJd, hasCl, readyOnly, pageSize]);

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
        {(
          [
            ["pending_review", "待處理"],
            ["applied", "已投遞"],
            ["interviewing", "面試中"],
            ["needs_manual_intervention", "需介入"],
            ["failed", "失敗"],
          ] as const
        ).map(([s, label]) => (
          <button
            key={s}
            className={`chip-btn ${statuses.includes(s) ? "active" : ""}`}
            onClick={() => toggleStatus(s)}
            title={`狀態：${label}（可多揀，再撳取消）`}
          >
            {label}
            {data.facets.statuses[s] !== undefined ? ` (${data.facets.statuses[s]})` : ""}
          </button>
        ))}
        <button
          className={`chip-btn ${readyOnly ? "active" : ""}`}
          onClick={() => setReadyOnly((v) => !v)}
          title="淨係睇「可以即刻投遞」：待處理 + 有 CL 已備 + 唔係外部網站"
        >
          ⚡ 可以即刻投遞
        </button>
        {(
          [
            ["offertoday", "OfferToday"],
            ["govhk_gbayes", "GovHK 大灣區"],
            ["govhk_it", "GovHK 資訊科技"],
            ["govhk_general", "GovHK 一般"],
          ] as const
        ).map(([p, label]) => (
          <button
            key={p}
            className={`chip-btn ${platforms.includes(p) ? "active" : ""}`}
            onClick={() => togglePlatform(p)}
            title={`平台：${label}（可多揀，再撳取消）`}
          >
            {label}
            {data.facets.platforms[p] !== undefined ? ` (${data.facets.platforms[p]})` : ""}
          </button>
        ))}
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
        <span className="filter-label">刊登日期：</span>
        <input
          type="date"
          className="chip-btn"
          value={postedFrom}
          onChange={(e) => setPostedFrom(e.target.value)}
          title="刊登日期：由"
          style={{ appearance: "auto" }}
        />
        <input
          type="date"
          className="chip-btn"
          value={postedTo}
          onChange={(e) => setPostedTo(e.target.value)}
          title="刊登日期：至"
          style={{ appearance: "auto" }}
        />
        <span className="filter-label">匹配度：</span>
        <input
          type="number"
          className="num-input"
          placeholder="≥ 分"
          min={0}
          max={100}
          value={minMatch}
          onChange={(e) => setMinMatch(e.target.value)}
          title="匹配度下限"
        />
        <input
          type="number"
          className="num-input"
          placeholder="≤ 分"
          min={0}
          max={100}
          value={maxMatch}
          onChange={(e) => setMaxMatch(e.target.value)}
          title="匹配度上限"
        />
        <label className="toggle-low" title="只顯示有完整 JD 嘅工">
          <input type="checkbox" checked={hasJd} onChange={(e) => setHasJd(e.target.checked)} />
          有 JD
        </label>
        <label className="toggle-low" title="只顯示已有 CL 嘅工">
          <input type="checkbox" checked={hasCl} onChange={(e) => setHasCl(e.target.checked)} />
          有 CL
        </label>
        <label className="toggle-low">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setShowAll(e.target.checked)}
          />
          顯示低匹配（{data.hidden_low_match}）
        </label>
        <button className="chip-btn" onClick={clearFilters} title="清除全部 filter">
          ✕ 清除全部
        </button>
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
