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
import { STATUS_LABEL } from "../types";
import type { Stats } from "../types";

const PLATFORM_COLORS: Record<string, string> = {
  jobsdb: "#c8102e",
  offertoday: "#0f6f68",
  govhk: "#d93b26",
};

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
    name: k,
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
                  <Cell key={p.name} fill={PLATFORM_COLORS[p.name] ?? "#625a4b"} />
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
