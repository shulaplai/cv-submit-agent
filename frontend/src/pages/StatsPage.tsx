import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import { PLATFORM_LABEL, STATUS_LABEL } from "../types";
import type { FunnelRow, Stats } from "../types";

const PLATFORM_COLORS: Record<string, string> = {
  jobsdb: "#c8102e",
  offertoday: "#0f6f68",
  govhk_gbayes: "#d93b26",
  govhk_it: "#007fac",
  govhk_general: "#6b8f4e",
  govhk: "#d93b26",
};

function FunnelTable({ title, note, rows }: { title: string; note: string; rows: FunnelRow[] }) {
  const hasData = rows.some((r) => r.applied > 0);
  return (
    <div className="chart-wrap">
      <h4>{title}</h4>
      <div className="note-inline" style={{ marginBottom: 8 }}>{note}</div>
      <table className="ledger">
        <thead>
          <tr>
            <th>組別</th>
            <th>已投</th>
            <th>有回覆</th>
            <th>面試</th>
            <th>冇回音</th>
            <th>落選</th>
            <th>Offer</th>
            <th>未更新</th>
            <th>回覆率</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td>{r.label}</td>
              <td>
                <b>{r.applied}</b>
              </td>
              <td>{r.responded}</td>
              <td>{r.interviewing}</td>
              <td>{r.no_response}</td>
              <td>{r.rejected}</td>
              <td>{r.offer}</td>
              <td style={{ opacity: 0.7 }}>{r.pending}</td>
              <td style={{ fontFamily: "var(--mono)" }}>
                {r.applied ? `${r.response_rate}%` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!hasData && (
        <div className="note-inline" style={{ marginTop: 8 }}>
          暫時未有已投遞記錄。投完之後喺職位詳情頁撳「面試中／冇回音／落選／Offer」就會入呢張表。
        </div>
      )}
    </div>
  );
}

export function StatsPage() {
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    api.stats().then(setStats).catch(console.error);
  }, []);

  if (!stats) return <div className="empty">載入中…</div>;

  const statusData = Object.entries(stats.by_status).map(([k, v]) => ({
    name: STATUS_LABEL[k as keyof typeof STATUS_LABEL] ?? k,
    value: v,
  }));
  const platformData = Object.entries(stats.by_platform).map(([k, v]) => ({
    key: k,
    name: PLATFORM_LABEL[k] ?? k,
    value: v,
  }));

  return (
    <>
      <div className="page-head">
        <div>
          <div className="kicker">Analytics · 統計</div>
          <h1>
            投遞<span className="stamp">統計</span>
          </h1>
        </div>
      </div>

      <div className="stat-strip">
        <div className="stat accent">
          <div className="n">{stats.applied_last_7d}</div>
          <div className="l">7 日內投遞</div>
        </div>
        <div className="stat teal">
          <div className="n">{stats.applied_last_30d}</div>
          <div className="l">30 日內投遞</div>
        </div>
        <div className="stat">
          <div className="n">
            {stats.weekly_applied.reduce((a, b) => a + b.count, 0)} <small>份</small>
          </div>
          <div className="l">近 8 週總投遞</div>
        </div>
        <div className="stat">
          <div className="n">{stats.total}</div>
          <div className="l">職位總數</div>
        </div>
      </div>

      <div className="grid2">
        <div className="chart-wrap">
          <h4>每週投遞量（近 8 週）</h4>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={stats.weekly_applied}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(25,22,17,0.12)" />
              <XAxis dataKey="week" tick={{ fontFamily: "var(--mono)", fontSize: 11 }} />
              <YAxis allowDecimals={false} tick={{ fontFamily: "var(--mono)", fontSize: 11 }} />
              <Tooltip
                contentStyle={{ background: "#191611", color: "#f2ecdf", border: "none", borderRadius: 8, fontFamily: "var(--mono)", fontSize: 12 }}
                cursor={{ fill: "rgba(217,59,38,0.08)" }}
              />
              <Bar dataKey="count" name="投遞" fill="#d93b26" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-wrap">
          <h4>平台分佈</h4>
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie data={platformData} dataKey="value" nameKey="name" innerRadius={55} outerRadius={85} paddingAngle={3}>
                {platformData.map((p) => (
                  <Cell key={p.key} fill={PLATFORM_COLORS[p.key] ?? "#625a4b"} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ background: "#191611", color: "#f2ecdf", border: "none", borderRadius: 8, fontFamily: "var(--mono)", fontSize: 12 }} />
              <Legend wrapperStyle={{ fontFamily: "var(--mono)", fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="chart-wrap">
          <h4>狀態分佈</h4>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={statusData} layout="vertical" margin={{ left: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(25,22,17,0.12)" />
              <XAxis type="number" allowDecimals={false} tick={{ fontFamily: "var(--mono)", fontSize: 11 }} />
              <YAxis type="category" dataKey="name" width={90} tick={{ fontFamily: "var(--sans)", fontSize: 12 }} />
              <Tooltip contentStyle={{ background: "#191611", color: "#f2ecdf", border: "none", borderRadius: 8, fontFamily: "var(--mono)", fontSize: 12 }} />
              <Bar dataKey="value" name="數量" fill="#0f6f68" radius={[0, 3, 3, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <FunnelTable
          title="結果漏斗：IT vs 一般"
          note="用嚟答「邊條軌值得繼續投」：回覆率 = （面試中＋落選＋Offer）÷ 已投。"
          rows={stats.funnel_by_category ?? []}
        />
        <FunnelTable
          title="結果漏斗：AI vs 非 AI"
          note="AI 相關職位（標題或 JD 提到 AI）同其他職位嘅回覆率比較。"
          rows={stats.funnel_by_ai ?? []}
        />
        <FunnelTable
          title="結果漏斗：按匹配度"
          note="睇下高分（≥65）係唔係真係有較高回覆率 —— 決定要唔要繼續收窄。"
          rows={stats.funnel_by_score ?? []}
        />

        <div className="chart-wrap">
          <h4>每週投遞一覽</h4>
          <table className="ledger">
            <thead>
              <tr>
                <th>週（開始日）</th>
                <th>投遞數</th>
              </tr>
            </thead>
            <tbody>
              {[...stats.weekly_applied].reverse().map((w) => (
                <tr key={w.week}>
                  <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{w.week}</td>
                  <td>
                    <b>{w.count}</b> 份
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
