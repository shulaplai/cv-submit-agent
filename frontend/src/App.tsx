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

  const startScan = useCallback(async () => {
    try {
      const r = await api.startScan();
      pushToast(r.message || "scan 已開始", "info");
      setTimeout(refreshScan, 1500);
    } catch (e) {
      pushToast(`scan 失敗: ${(e as Error).message}`, "err");
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
                ▣ 掃描進行中
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
                <br />
                新職位 <b>{scan.last.new_jobs}</b> · 重複 {scan.last.skipped_duplicates} · 掃到{" "}
                {scan.last.scanned}
                {scan.last.backfilled > 0 && (
                  <>
                    <br />
                    補齊 {scan.last.backfilled} 份
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
          <button className="btn ink" onClick={startScan} disabled={scan?.running}>
            {scan?.running ? "掃描中…" : "▶ 立即掃描"}
          </button>
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
