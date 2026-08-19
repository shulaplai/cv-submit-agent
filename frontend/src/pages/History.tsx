import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Modal, PlatformBadge, StatusChip, fmtDate, latestCL } from "../components";
import type { Job } from "../types";

export function History() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Job | null>(null);
  const [tab, setTab] = useState<"applied" | "all">("applied");

  const load = useCallback(async () => {
    try {
      const r = await api.listJobs({
        status: tab === "applied" ? "applied" : undefined,
        show_all: tab === "all",
        limit: 500,
      });
      const applied = r.items.filter((j) => j.applied_at);
      setJobs(applied);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    load();
  }, [load]);

  const open = async (id: number) => {
    try {
      setSelected(await api.getJob(id));
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="kicker">Archive · 投遞檔案</div>
          <h1>
            投遞<span className="stamp">歷史</span>
          </h1>
        </div>
        <div className="filterbar" style={{ marginBottom: 0 }}>
          <button className={`chip-btn ${tab === "applied" ? "active" : ""}`} onClick={() => setTab("applied")}>
            已投遞
          </button>
          <button className={`chip-btn ${tab === "all" ? "active" : ""}`} onClick={() => setTab("all")}>
            全部（含進行中）
          </button>
        </div>
      </div>

      {loading ? (
        <div className="empty">載入中…</div>
      ) : jobs.length === 0 ? (
        <div className="empty">未有投遞紀錄 — 投完一份工，喺職位台撳「記錄已投遞」就會喺度出現。</div>
      ) : (
        <div className="chart-wrap" style={{ padding: 0, overflow: "hidden" }}>
          <table className="ledger">
            <thead>
              <tr>
                <th>投遞日期</th>
                <th>平台</th>
                <th>職位 / 公司</th>
                <th>狀態</th>
                <th>CL</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => {
                const cl = latestCL(j);
                return (
                  <tr key={j.id} onClick={() => open(j.id)} style={{ cursor: "pointer" }}>
                    <td style={{ whiteSpace: "nowrap", fontFamily: "var(--mono)", fontSize: 12 }}>
                      {fmtDate(j.applied_at)}
                    </td>
                    <td>
                      <PlatformBadge platform={j.platform} />
                    </td>
                    <td>
                      <div className="row-title">{j.title}</div>
                      <div style={{ color: "var(--ink-soft)", fontSize: 12.5 }}>{j.company}</div>
                    </td>
                    <td>
                      <StatusChip status={j.status} />
                    </td>
                    <td>
                      {cl ? (
                        <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--teal)" }}>
                          v{cl.version} · {cl.language === "zh" ? "中文" : "English"} ✓
                        </span>
                      ) : (
                        <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--ink-faint)" }}>—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <Modal onClose={() => setSelected(null)}>
          <PlatformBadge platform={selected.platform} /> <StatusChip status={selected.status} />
          <h2>{selected.title}</h2>
          <div className="dmeta">
            <span>{selected.company}</span>
            <span>{selected.location}</span>
            <span>投遞於 {fmtDate(selected.applied_at)}</span>
          </div>
          <div className="section">
            <h4>當時嘅 JD 備份</h4>
            <div className="jd-body">{selected.jd_text || "（無 JD 快照）"}</div>
          </div>
          <div className="section cl-editor">
            <h4>Cover Letter 存檔</h4>
            {selected.cover_letters?.length ? (
              selected.cover_letters.map((cl) => (
                <div key={cl.id} style={{ marginBottom: 14 }}>
                  <div className="note-inline" style={{ borderStyle: "solid" }}>
                    v{cl.version} · {cl.language === "zh" ? "中文" : "English"} · {fmtDate(cl.created_at)}
                  </div>
                  <div className="jd-body" style={{ marginTop: 8, maxHeight: 260 }}>
                    {cl.content}
                  </div>
                </div>
              ))
            ) : (
              <div className="note-inline">冇 CL 存檔</div>
            )}
          </div>
          {selected.notes && (
            <div className="section">
              <h4>備註</h4>
              <div className="jd-body">{selected.notes}</div>
            </div>
          )}
        </Modal>
      )}
    </>
  );
}
