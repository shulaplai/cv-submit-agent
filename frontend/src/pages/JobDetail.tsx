import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Modal, PlatformBadge, StatusChip, fmtDate } from "../components";
import type { Job } from "../types";

interface ApplyResult {
  ok: boolean;
  kind: string;
  url?: string;
  to?: string;
  message: string;
  preview?: { to: string; subject: string; body: string };
}

export function JobDetail({
  job,
  onClose,
  onChanged,
  pushToast,
}: {
  job: Job;
  onClose: () => void;
  onChanged: (job: Job) => void;
  pushToast: (text: string, kind?: "info" | "ok" | "err") => void;
}) {
  const [clText, setClText] = useState("");
  const [clLang, setClLang] = useState("en");
  const [verIndex, setVerIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; kind: string } | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const [notes, setNotes] = useState(job.notes);
  const [stage, setStage] = useState(job.interview_stage);

  const versions = useMemo(() => [...(job.cover_letters || [])], [job.cover_letters]);

  useEffect(() => {
    if (versions.length) {
      const last = versions.length - 1;
      setVerIndex(last);
      setClText(versions[last].content);
      setClLang(versions[last].language);
    } else {
      setClText("");
    }
  }, [versions]);

  const selectVer = (i: number) => {
    setVerIndex(i);
    setClText(versions[i].content);
    setClLang(versions[i].language);
  };

  const showNote = (text: string, kind = "info") => {
    setNote({ text, kind });
    setTimeout(() => setNote(null), 9000);
  };

  const refresh = useCallback(
    async (id: number) => {
      const fresh = await api.getJob(id);
      onChanged(fresh);
      return fresh;
    },
    [onChanged]
  );

  const doRefresh = async () => {
    setBusy(true);
    try {
      await api.refreshJob(job.id);
      const fresh = await refresh(job.id);
      setVerIndex(Math.max(0, fresh.cover_letters.length - 1));
      setClText(fresh.cover_letters[fresh.cover_letters.length - 1]?.content ?? "");
      showNote("JD 已載入，match score 同 CL 已更新。", "ok");
    } catch (e) {
      showNote(`失敗: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  };

  const saveCL = async () => {
    if (!clText.trim()) return;
    setBusy(true);
    try {
      await api.saveCoverLetter(job.id, clText);
      await refresh(job.id);
      showNote("已儲存為新版本（舊版保留）。", "ok");
    } catch (e) {
      showNote(`儲存失敗: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async (instructions: string) => {
    setBusy(true);
    try {
      await api.regenerateCL(job.id, instructions);
      await refresh(job.id);
      showNote("已生成新版 CL。", "ok");
    } catch (e) {
      showNote(`生成失敗: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  };

  const startApply = async () => {
    setBusy(true);
    try {
      const r = await api.apply(job.id);
      setApplyResult(r as ApplyResult);
      showNote(r.message, r.ok ? "ok" : "err");
    } catch (e) {
      showNote(`開申請失敗: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  };

  const markApplied = async () => {
    setBusy(true);
    try {
      await api.markApplied(job.id);
      await refresh(job.id);
      showNote("已記錄：已投遞 ✔", "ok");
    } catch (e) {
      showNote(`記錄失敗: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  };

  const saveMeta = async () => {
    setBusy(true);
    try {
      await api.updateJob(job.id, { notes, interview_stage: stage });
      await refresh(job.id);
      showNote("備註／面試進度已儲存。", "ok");
    } catch (e) {
      showNote(`儲存失敗: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  };

  const isGBA = job.platform === "govhk";
  const applyBtnLabel =
    job.apply_method === "email"
      ? "✉ Email 申請"
      : job.apply_method === "external_link"
        ? "↗ 外部申請 link"
        : job.platform === "offertoday"
          ? "✎ 傳送投遞消息"
          : "▶ 開始申請";

  return (
    <Modal onClose={onClose}>
      <PlatformBadge platform={job.platform} />{" "}
      <StatusChip status={job.status} />
      {job.applied_at && (
        <span style={{ float: "right" }}>
          <span className="stamp-tag">已投遞 {fmtDate(job.applied_at)}</span>
        </span>
      )}
      <h2>{job.title}</h2>
      <div className="dmeta">
        <span>{job.company || "—"}</span>
        <span>{job.location || "—"}</span>
        <span>{job.salary_range || "薪酬不詳"}</span>
        <span>match {job.match_score}/100</span>
        {job.posted_at && <span>刊登 {job.posted_at}</span>}
      </div>
      {job.match_reason && (
        <div className="note-inline">匹配理由：{job.match_reason}</div>
      )}
      {isGBA && (
        <div className="note-inline" style={{ borderStyle: "solid", borderColor: "var(--gold)" }}>
          ⚠ 大灣區青年就業計劃職位：須 29 歲或以下、副學位或以上學歷、可合法喺香港及內地受僱。
        </div>
      )}
      {job.contact_email && (
        <div className="note-inline" style={{ borderStyle: "solid" }}>
          聯絡：{job.contact_person || "—"} · {job.contact_email}
        </div>
      )}

      <div className="section">
        <h4>職位描述（JD 快照）</h4>
        {job.jd_text ? (
          <div className="jd-body">{job.jd_text}</div>
        ) : (
          <div className="note-inline">
            JD 未載入（列表快照）。{" "}
            <button className="btn" onClick={doRefresh} disabled={busy}>
              載入 JD + 生成 CL
            </button>
          </div>
        )}
      </div>

      <div className="section cl-editor">
        <h4>Cover Letter {versions.length ? `· v${versions[verIndex]?.version}（${clLang === "zh" ? "中文" : "English"}）` : ""}</h4>
        {versions.length > 1 && (
          <div className="cl-versions">
            {versions.map((v, i) => (
              <button
                key={v.id}
                className={`ver ${i === verIndex ? "active" : ""}`}
                onClick={() => selectVer(i)}
              >
                v{v.version}
              </button>
            ))}
          </div>
        )}
        <textarea
          value={clText}
          onChange={(e) => setClText(e.target.value)}
          placeholder={versions.length ? "" : "未生成 CL — 撳「生成 CL」或先載入 JD"}
        />
        <div className="btnrow" style={{ marginTop: 10 }}>
          {!versions.length && (
            <button className="btn primary" onClick={() => regenerate("")} disabled={busy || !job.jd_text}>
              生成 CL
            </button>
          )}
          <button className="btn" onClick={saveCL} disabled={busy || !clText.trim()}>
            儲存新版本
          </button>
          <button
            className="btn"
            onClick={() => {
              const inst = prompt("額外指示（例如：呢段太長，改短啲；強調我嘅 LangGraph 經驗）：");
              if (inst !== null) regenerate(inst);
            }}
            disabled={busy || !job.jd_text}
          >
            重新生成…
          </button>
        </div>
      </div>

      <div className="section">
        <h4>申請動作</h4>
        <div className="btnrow">
          {job.apply_method === "external_link" ? (
            <a className="btn primary" href={job.external_url || job.url} target="_blank" rel="noreferrer">
              {applyBtnLabel}
            </a>
          ) : (
            <button className="btn primary" onClick={startApply} disabled={busy}>
              {applyBtnLabel}
            </button>
          )}
          <button className="btn teal" onClick={markApplied} disabled={busy || job.status === "applied"}>
            ✔ 記錄已投遞
          </button>
        </div>
        {applyResult && (
          <div className={`note-inline ${applyResult.ok ? "ok" : "err"}`}>
            {applyResult.message}
            {applyResult.preview && (
              <div style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>
                <b>收件人：</b>
                {applyResult.preview.to}
                <br />
                <b>主旨：</b>
                {applyResult.preview.subject}
                <br />
                <b>內文：</b>
                {applyResult.preview.body}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="section">
        <h4>追蹤</h4>
        <div className="field">
          <label>面試進度</label>
          <select value={stage} onChange={(e) => setStage(e.target.value)}>
            <option value="">未開始</option>
            <option value="phone_screen">電話篩選</option>
            <option value="first_interview">第一輪面試</option>
            <option value="second_interview">第二輪面試</option>
            <option value="technical">技術測試</option>
            <option value="offer_discussion">傾 offer</option>
          </select>
        </div>
        <div className="field">
          <label>備註</label>
          <textarea
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="例如：recruiter 覆咗 email、下輪係 video call…"
          />
        </div>
        <button className="btn" onClick={saveMeta} disabled={busy}>
          儲存追蹤
        </button>
        <div className="btnrow" style={{ marginTop: 14 }}>
          <button className="btn" onClick={doRefresh} disabled={busy}>
            ↻ 重新整理（重新 match + CL）
          </button>
          <a className="btn" href={job.url} target="_blank" rel="noreferrer">
            ↗ 開原頁
          </a>
        </div>
      </div>

      {note && <div className={`note-inline ${note.kind === "err" ? "err" : note.kind === "ok" ? "ok" : ""}`}>{note.text}</div>}
    </Modal>
  );
}
