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
  // 「搵更適合自己嘅工」篩選（AI／合約／外派／資歷）
  const [aiOnly, setAiOnly] = useState(false);
  const [noContract, setNoContract] = useState(false);
  const [noAgency, setNoAgency] = useState(false);
  const [levelFit, setLevelFit] = useState(false);
  const [category, setCategory] = useState<"it" | "general" | "">("it");
  const [q, setQ] = useState("");
  const [showAll, setShowAll] = useState(true); // 預設顯示全部（包括低匹配工）
  // 預設「focus」= AI 優先 -> 高分 -> 新鮮（IT 頁）；切去一般頁會轉返「updated」
  const [sort, setSort] = useState("focus");
  const [shortlist, setShortlist] = useState<Job[]>([]);
  // 批量 AI 檢查（LLM 分批搵出唔係 IT 嘅工）
  const [aiPending, setAiPending] = useState(0);
  const [aiRun, setAiRun] = useState<{
    running: boolean; checked: number; total: number; batches: number;
    non_it: number; it_ai: number; failed_batches: number; marked_ids: number[];
  } | null>(null);
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
    setAiOnly(false);
    setNoContract(false);
    setNoAgency(false);
    setLevelFit(false);
    setQ("");
    setShowAll(true); // 清除 filter 後回復「顯示全部（包括低匹配）」
    setSort(category === "general" ? "updated" : "focus");
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
      const [jobs, s, picks] = await Promise.all([
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
          ai_only: aiOnly || undefined,
          exclude_contract: noContract || undefined,
          exclude_agency: noAgency || undefined,
          levels: levelFit ? "fit" : undefined,
          limit: pageSize,
          offset: (page - 1) * pageSize,
        }),
        api.stats(),
        // ✦ 今日精選：AI 優先 + ≥65 分 + CL 已備（唔阻住下面嘅列表）
        api.listJobs({
          category: "it", sort: "focus", min_match: 65,
          ready_to_apply: true, show_all: false, limit: 5,
        }),
      ]);
      setShortlist(picks.items);
      setData(jobs);
      api.aiCheckPending().then((p) => setAiPending(p.pending)).catch(() => {});
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
      aiOnly, noContract, noAgency, levelFit, page, pageSize, pushToast]);

  useEffect(() => {
    load();
  }, [load]);

  // any filter change -> back to page 1
  useEffect(() => {
    setPage(1);
  }, [statuses, platforms, category, q, showAll, sort, addedFrom, addedTo,
      postedFrom, postedTo, minMatch, maxMatch, hasJd, hasCl, readyOnly,
      aiOnly, noContract, noAgency, levelFit, pageSize]);

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

  // 用戶痛點：173 份 IT 工從來冇評分（match_score 0）→ 排序永遠沉底。
  // 一撳分批補返（每批 30 份，會用 LLM）。
  const doScoreUnscored = async () => {
    try {
      const r = await api.backfill({ scope: "it_unscored", limit: 30 });
      pushToast(r.message || "開始補齊未評分 IT 工", "info");
      setTimeout(load, 3000);
    } catch (e) {
      pushToast(`補齊失敗: ${(e as Error).message}`, "err");
    }
  };

  const applyShortlist = async () => {
    const ids = shortlist.map((j) => j.id);
    if (!ids.length) return;
    try {
      const r = await api.batchApply(ids);
      pushToast(r.message, "info");
      setBatch({ running: true, total: r.total, done: 0, results: [] });
      setShowBatchResult(false);
    } catch (e) {
      pushToast(`一齊投遞失敗: ${(e as Error).message}`, "err");
    }
  };

  // 批量 AI 檢查：分批（預設 40 份/call）搵出「其實唔係 IT」嘅工，標低匹配
  const runAiCheck = async () => {
    if (!aiPending) {
      pushToast("所有 IT 工都已經 AI 檢查過。", "info");
      return;
    }
    try {
      const r = await api.startAiCheck();
      pushToast(r.message, "info");
      setAiRun({ running: true, checked: 0, total: 0, batches: 0, non_it: 0,
                 it_ai: 0, failed_batches: 0, marked_ids: [] });
    } catch (e) {
      pushToast(`AI 檢查啟動失敗: ${(e as Error).message}`, "err");
    }
  };

  const resetAiCheck = async () => {
    try {
      const r = await api.resetAiCheck();
      pushToast(`已還原 ${r.reset} 份 AI 檢查結果（會重新檢查）。`, "ok");
      load();
    } catch (e) {
      pushToast(`還原失敗: ${(e as Error).message}`, "err");
    }
  };

  useEffect(() => {
    if (!aiRun?.running) return undefined;
    const t = window.setInterval(async () => {
      try {
        const s = await api.aiCheckStatus();
        setAiRun(s);
        if (!s.running) {
          window.clearInterval(t);
          pushToast(
            `AI 檢查完成：睇咗 ${s.checked} 份，判非 IT ${s.non_it} 份（已標低匹配），AI 工 ${s.it_ai} 份。`,
            "ok",
          );
          load();
        }
      } catch {
        /* ignore */
      }
    }, 2500);
    return () => window.clearInterval(t);
  }, [aiRun?.running, load, pushToast]);

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
          onClick={() => {
            setCategory("it");
            setSort("focus");       // IT 頁預設 AI 優先
          }}
        >
          <b>IT</b> 職位
        </button>
        <button
          className={`track-tab ${category === "general" ? "active" : ""}`}
          onClick={() => {
            setCategory("general");
            setSort("updated");     // 一般頁維持原本排序
          }}
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
            ["low_match", "低匹配"],
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
        <button
          className={`chip-btn ${aiOnly ? "active" : ""}`}
          onClick={() => setAiOnly((v) => !v)}
          title="淨係睇 AI／agent 相關職位（標題或 JD 提到 AI／agent；字眼可喺設定頁改）"
        >
          ✦ AI／agent 職位
          {data.facets.ai !== undefined ? ` (${data.facets.ai})` : ""}
        </button>
        <button
          className={`chip-btn ${noContract ? "active" : ""}`}
          onClick={() => setNoContract((v) => !v)}
          title="唔要合約／臨時／兼職／實習（想搵穩定長工）"
        >
          ✕ 合約／臨時
          {data.facets.contract !== undefined ? ` (${data.facets.contract})` : ""}
        </button>
        <button
          className={`chip-btn ${noAgency ? "active" : ""}`}
          onClick={() => setNoAgency((v) => !v)}
          title="唔要外派／獵頭／人力資源公司（EA）"
        >
          ✕ 外派／獵頭
          {data.facets.agency !== undefined ? ` (${data.facets.agency})` : ""}
        </button>
        <button
          className={`chip-btn ${levelFit ? "active" : ""}`}
          onClick={() => setLevelFit((v) => !v)}
          title="只睇資歷啱你（AI 判斷：唔會要 5 年+/senior，亦唔係見習）。未評分嘅工唔會出現喺呢個篩選。"
        >
          ✓ 資歷啱
          {data.facets.levels?.fit !== undefined ? ` (${data.facets.levels.fit})` : ""}
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
          <option value="focus">排序：✦ AI 優先 + 高分 + 新鮮</option>
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
        <label className="toggle-low" title="預設顯示全部（包括低匹配）；唔勾就隱藏低匹配工">
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
        <button className="btn" onClick={doBackfill} title="為最舊嘅未處理職位補上 JD / CL（會用 LLM）">
          ⇪ 補齊
        </button>
        <button
          className="btn primary"
          onClick={runAiCheck}
          disabled={aiRun?.running}
          title="分批（每 40 份一個 call）用 AI 判斷邊啲職位其實唔係 IT／AI 工，判非 IT 會標低匹配（可以還原）"
        >
          {aiRun?.running
            ? `🤖 AI 檢查中 ${aiRun.checked}/${aiRun.total || "…"}`
            : `🤖 批量 AI 檢查${aiPending ? `（${aiPending} 份未檢查）` : ""}`}
        </button>
        {aiRun && !aiRun.running && aiRun.checked > 0 && (
          <button className="chip-btn" onClick={resetAiCheck} title="清空 AI 判定，令佢下次再檢查（唔會刪工）">
            ↩ 還原 AI 判定
          </button>
        )}
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

      {shortlist.length > 0 && (
        <div className="batch-bar" style={{ flexWrap: "wrap", gap: 10 }}>
          <span>
            ✦ <b>今日精選</b>
            <span className="filter-label" style={{ marginLeft: 6 }}>
              AI 優先 · ≥65 分 · CL 已備
            </span>
          </span>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, flex: 1 }}>
            {shortlist.map((j) => (
              <button
                key={j.id}
                className="chip-btn"
                onClick={() => openDetail(j)}
                title={`${j.company || "—"}｜${j.match_reason || ""}`}
              >
                {j.ai_match ? "✦ " : ""}
                {j.title.length > 26 ? `${j.title.slice(0, 26)}…` : j.title}
                {" · "}
                {j.match_score}
              </button>
            ))}
          </div>
          <button className="btn primary" onClick={applyShortlist} title="一次過自動投遞呢幾份">
            ▶ 一次過投遞
          </button>
          <button className="btn" onClick={doScoreUnscored} title="為未評分（0 分）嘅 IT 工補評分＋生成 CL，每次 30 份（會用 LLM）">
            ⇪ 補齊未評分 IT 工
          </button>
        </div>
      )}

      {category === "general" && !loading && (
        <div className="note-inline" style={{ marginBottom: 10 }}>
          一般工預設<b>唔會洗 LLM 評分</b>（只有 IT 工需要）—— 佢哋照樣有 JD 同 AI／合約／外派標籤，
          分數係關鍵字重疊分（所以多數係 0 分）。想評分就撳入去詳情頁「↻ 重新整理」，
          或者去設定頁開「一般工都要 LLM 評分」。
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
                  {job.location_uncertain && (
                    <span
                      className="chip low"
                      title="JD 同列表都冇寫明地區，唔喺「想去嘅地點」名單內搵到——已保留畀你人手睇"
                    >
                      ⚠ 地點未確定
                    </span>
                  )}
                  <span>{job.salary_range || "薪酬不詳"}</span>
                  <span title="入庫日期（入咗職位台嘅日子）">入庫 {fmtDate(job.created_at)}</span>
                  {job.match_reason.includes("未 LLM 評分") && (
                    <span
                      className="chip"
                      title="一般工預設唔洗 LLM 評分（省 API）。想評分：撳入詳情頁「↻ 重新整理」"
                    >
                      未評分
                    </span>
                  )}
                  {job.ai_match && (
                    <span
                      className={`chip ${job.ai_strength === "title" ? "ok" : ""}`}
                      title={
                        job.ai_strength === "title"
                          ? "AI 相關（標題有 AI／agent）—— 呢類工 scan 到一定要收"
                          : "只喺 JD 提到 AI／agent（標題冇）"
                      }
                    >
                      {job.ai_strength === "title" ? "✦ AI／agent" : "AI（JD）"}
                    </span>
                  )}
                  {job.is_contract && (
                    <span className="chip low" title="合約／臨時／兼職／實習">
                      合約／臨時
                    </span>
                  )}
                  {job.is_agency && (
                    <span className="chip low" title="外派／獵頭／人力資源公司">
                      外派／獵頭
                    </span>
                  )}
                  {job.match_level === "over" && (
                    <span className="chip low" title={job.match_reason}>
                      資歷超出
                    </span>
                  )}
                  {job.match_level === "under" && (
                    <span className="chip" title={job.match_reason}>
                      資歷有餘
                    </span>
                  )}
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
                  <span
                    className={`cl-ready ${job.cv_variant === "通用版" ? "no" : ""}`}
                    title="申請時會交邊份 CV（跟職位標題揀版本）"
                  >
                    CV：{job.cv_variant || "通用版"}
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
