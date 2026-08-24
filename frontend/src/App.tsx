import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { ToastHost, fmtDate } from "./components";
import { Dashboard } from "./pages/Dashboard";
import { History } from "./pages/History";
import { Settings } from "./pages/Settings";
import { StatsPage } from "./pages/StatsPage";
import type { ScanStatus } from "./types";

type View = "dashboard" | "history" | "stats" | "settings";

interface Toast {
  id: number;
  text: string;
  kind: "info" | "ok" | "err";
}

export default function App() {
  const [view, setView] = useState<View>("dashboard");
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [scanTrack, setScanTrack] = useState<"all" | "it" | "general">("all");
  const [kwIt, setKwIt] = useState("");
  const [kwGeneral, setKwGeneral] = useState("");
  const [kwOpen, setKwOpen] = useState(false);
  const [profileLoaded, setProfileLoaded] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);

  const pushToast = useCallback((text: string, kind: "info" | "ok" | "err" = "info") => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 7000);
  }, []);

  const refreshScan = useCallback(async () => {
    try {
      setScan(await api.scanStatus());
    } catch {
      /* backend not ready yet */
    }
  }, []);

  useEffect(() => {
    refreshScan();
    const t = setInterval(refreshScan, 4000);
    return () => clearInterval(t);
  }, [refreshScan]);

  // load the profile once for the scan-time keyword editor
  useEffect(() => {
    api.profile().then((p) => {
      setKwIt(p.it_keywords);
      setKwGeneral(p.general_job_keywords);
      setProfileLoaded(true);
    }).catch(() => {});
  }, []);

  const startScan = useCallback(async () => {
    try {
      // 掃描前：如果關鍵字改過，先儲存入設定（下次 scan 都用同一套）
      if (profileLoaded) {
        const p = await api.profile();
        if (kwIt !== p.it_keywords || kwGeneral !== p.general_job_keywords) {
          await api.saveProfile({ it_keywords: kwIt, general_job_keywords: kwGeneral });
          pushToast("掃描關鍵字已儲存到設定。", "ok");
        }
      }
      const r = await api.startScan(scanTrack);
      pushToast(r.message || "scan 已開始", "info");
      setTimeout(refreshScan, 1500);
    } catch (e) {
      pushToast(`scan 失敗: ${(e as Error).message}`, "err");
    }
  }, [pushToast, refreshScan, scanTrack, kwIt, kwGeneral, profileLoaded]);

  const stopScan = useCallback(async () => {
    try {
      const r = await api.stopScan();
      pushToast(r.message || "已要求暫停", "info");
      setTimeout(refreshScan, 1000);
    } catch (e) {
      pushToast(`暫停失敗: ${(e as Error).message}`, "err");
    }
  }, [pushToast, refreshScan]);

  const nav = [
    { id: "dashboard" as View, label: "職位台", idx: "01" },
    { id: "history" as View, label: "投遞檔案", idx: "02" },
    { id: "stats" as View, label: "統計", idx: "03" },
    { id: "settings" as View, label: "設定", idx: "04" },
  ];

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="wordmark">
          <div className="mark">
            CV<em>·</em>SUBMIT
          </div>
          <div className="sub">求職運作台 · Agent</div>
        </div>
        {nav.map((n) => (
          <div
            key={n.id}
            className={`nav-item ${view === n.id ? "active" : ""}`}
            onClick={() => setView(n.id)}
          >
            <span className="idx">{n.idx}</span>
            {n.label}
          </div>
        ))}
        <div className="scan-box">
          <div className="scan-status">
            {scan?.running ? (
              <>
                ▣ 掃描進行中{scan.stop_requested ? "（暫停請求已送出…）" : ""}
                {scan.progress?.platform && (
                  <>
                    <br />
                    正在掃 {scan.progress.platform}…（{scan.progress.phase}）
                  </>
                )}
              </>
            ) : scan?.last ? (
              <>
                上次掃描 <b>{fmtDate(scan.last.at)}</b>
                {scan.last.stopped && (
                  <>
                    {" "}
                    <span className="err">（已暫停）</span>
                  </>
                )}
                <br />
                新職位 <b>{scan.last.new_jobs}</b> · 重複 {scan.last.skipped_duplicates} · 掃到{" "}
                {scan.last.scanned}
                {scan.last.tracks && (
                  <>
                    {scan.last.tracks.it && (
                      <>
                        <br />
                        IT：新 {scan.last.tracks.it.new_jobs} / 掃到 {scan.last.tracks.it.scanned}
                        {scan.last.tracks.it.skipped_old > 0 && ` · 過期 ${scan.last.tracks.it.skipped_old}`}
                      </>
                    )}
                    {scan.last.tracks.general && (
                      <>
                        <br />
                        一般：新 {scan.last.tracks.general.new_jobs} / 掃到 {scan.last.tracks.general.scanned}
                        {scan.last.tracks.general.skipped_old > 0 && ` · 過期 ${scan.last.tracks.general.skipped_old}`}
                      </>
                    )}
                  </>
                )}
                {scan.last.skipped_old > 0 && (
                  <>
                    <br />
                    過期（超過兩個月）已略過 {scan.last.skipped_old} 份
                  </>
                )}
                {scan.last.capped > 0 && (
                  <>
                    <br />
                    上限已滿，另有 {scan.last.capped} 份等下次 scan
                  </>
                )}
                {scan.last.backfilled > 0 && (
                  <>
                    <br />
                    補齊 {scan.last.backfilled} 份
                  </>
                )}
                {scan.last.details_fetched > 0 && (
                  <>
                    <br />
                    已攞 JD {scan.last.details_fetched} 份
                  </>
                )}
                {scan.last.errors.length > 0 && (
                  <>
                    <br />
                    <span className="err">⚠ {scan.last.errors.length} 個平台出錯</span>
                  </>
                )}
              </>
            ) : (
              <>未掃描過</>
            )}
          </div>
          <div className="scan-actions">
            <button className="btn ink" onClick={startScan} disabled={scan?.running}>
              {scan?.running ? "掃描中…" : "▶ 立即掃描"}
            </button>
            <button
              className="btn"
              onClick={stopScan}
              disabled={!scan?.running || scan?.stop_requested}
              title="中斷 scan；已掃到嘅內容照樣入庫"
            >
              ⏸ 暫停
            </button>
          </div>
          <select
            className="chip-btn scan-track-select"
            value={scanTrack}
            disabled={scan?.running}
            onChange={(e) => setScanTrack(e.target.value as "all" | "it" | "general")}
            style={{ appearance: "auto", width: "100%", marginTop: 8 }}
            title="掃描範圍：全部（跟設定）／淨 IT／淨一般"
          >
            <option value="all">掃描範圍：全部（IT + 一般）</option>
            <option value="it">淨掃描：IT 職位</option>
            <option value="general">淨掃描：一般職位</option>
          </select>
          <button
            className="btn"
            onClick={() => setKwOpen((v) => !v)}
            disabled={scan?.running}
            style={{ width: "100%", marginTop: 8 }}
            title="掃描前可以改關鍵字；撳掃描時會儲存到設定"
          >
            {kwOpen ? "收起 ✎ 掃描關鍵字" : "✎ 掃描關鍵字"}
          </button>
          {kwOpen && (
            <div className="scan-kw">
              <label>
                IT 關鍵字
                <input
                  value={kwIt}
                  onChange={(e) => setKwIt(e.target.value)}
                  placeholder="ai, developer, engineer, 程式, 工程師…（留空 = 預設）"
                />
              </label>
              <label>
                一般關鍵字
                <input
                  value={kwGeneral}
                  onChange={(e) => setKwGeneral(e.target.value)}
                  placeholder="文員, 行政助理, 客戶服務…（留空 = 預設）"
                />
              </label>
              <div className="note-inline" style={{ fontSize: 11, marginTop: 4 }}>
                改完直接撳「立即掃描」——會自動儲存到設定，之後每次 scan 都用呢套。
              </div>
            </div>
          )}
        </div>
      </aside>

      <main className="main">
        {view === "dashboard" && <Dashboard pushToast={pushToast} onOpenHistory={() => setView("history")} />}
        {view === "history" && <History />}
        {view === "stats" && <StatsPage />}
        {view === "settings" && <Settings pushToast={pushToast} />}
      </main>

      <ToastHost toasts={toasts} />
    </div>
  );
}
