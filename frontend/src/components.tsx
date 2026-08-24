import { STATUS_LABEL, type Job, type Status } from "./types";

export function StatusChip({ status }: { status: Status }) {
  return <span className={`status-chip ${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function PlatformBadge({ platform }: { platform: string }) {
  const label: Record<string, string> = {
    jobsdb: "JobsDB",
    offertoday: "OfferToday",
    govhk_gbayes: "GovHK · 大灣區計劃",
    govhk_it: "GovHK · 資訊及科技界",
    govhk_general: "GovHK · 一般職位",
    govhk: "GovHK",
  };
  return <span className={`pbadge ${platform}`}>{label[platform] ?? platform}</span>;
}

export function ScoreRing({ score }: { score: number }) {
  const cls = score >= 65 ? "hi" : score >= 50 ? "mid" : "lo";
  return (
    <div className={`score-ring ${cls}`} title={`match score ${score}/100`}>
      {score}
    </div>
  );
}

export function Modal({ onClose, children }: { onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-x" onClick={onClose} aria-label="close">
          ✕
        </button>
        {children}
      </div>
    </div>
  );
}

interface ToastItem {
  id: number;
  text: string;
  kind: "info" | "ok" | "err";
}

export function ToastHost({ toasts }: { toasts: ToastItem[] }) {
  if (!toasts.length) return null;
  return (
    <div className="toast-wrap">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("zh-HK", { year: "numeric", month: "2-digit", day: "2-digit" });
}

export function latestCL(job: Job): { content: string; version: number; language: string } | null {
  if (!job.cover_letters?.length) return null;
  const cl = job.cover_letters[job.cover_letters.length - 1];
  return { content: cl.content, version: cl.version, language: cl.language };
}
