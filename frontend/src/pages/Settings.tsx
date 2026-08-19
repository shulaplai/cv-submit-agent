import { useEffect, useState } from "react";
import { api } from "../api";
import type { Profile } from "../types";

export function Settings({ pushToast }: { pushToast: (text: string, kind?: "info" | "ok" | "err") => void }) {
  const [p, setP] = useState<Profile | null>(null);
  const [skillsText, setSkillsText] = useState("");

  useEffect(() => {
    api.profile().then((profile) => {
      setP(profile);
      setSkillsText(profile.skills_json);
    }).catch(console.error);
  }, []);

  if (!p) return <div className="empty">載入中…</div>;

  const set = (k: keyof Profile, v: string | boolean) => setP({ ...p, [k]: v });

  const save = async () => {
    try {
      const updated = await api.saveProfile({
        name: p.name,
        email: p.email,
        cv_en_path: p.cv_en_path,
        cv_zh_path: p.cv_zh_path,
        skills_json: skillsText,
        gba_age_under_29: p.gba_age_under_29,
        gba_edu_associate_degree: p.gba_edu_associate_degree,
      });
      setP(updated);
      setSkillsText(updated.skills_json);
      pushToast("設定已儲存。", "ok");
    } catch (e) {
      pushToast(`儲存失敗: ${(e as Error).message}`, "err");
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <div className="kicker">Settings · 設定</div>
          <h1>
            申請者<span className="stamp">設定</span>
          </h1>
        </div>
      </div>

      <div className="detail" style={{ maxWidth: 640 }}>
        <div className="field">
          <label>姓名（Email 申請簽名用）</label>
          <input value={p.name} onChange={(e) => set("name", e.target.value)} placeholder="例如：陳大文" />
        </div>
        <div className="field">
          <label>Email</label>
          <input value={p.email} onChange={(e) => set("email", e.target.value)} placeholder="你嘅聯絡 email" />
        </div>
        <div className="field">
          <label>英文 CV（PDF 路徑）</label>
          <input value={p.cv_en_path} onChange={(e) => set("cv_en_path", e.target.value)} placeholder="/Users/you/Desktop/CV_en.pdf" />
        </div>
        <div className="field">
          <label>中文 CV（PDF 路徑）— gov.hk 中文 JD 用呢份</label>
          <input value={p.cv_zh_path} onChange={(e) => set("cv_zh_path", e.target.value)} placeholder="/Users/you/Desktop/CV_zh.pdf" />
        </div>
        <div className="field">
          <label>技能清單（JSON 陣列，用嚟 match score 同 CL）</label>
          <textarea
            rows={4}
            value={skillsText}
            onChange={(e) => setSkillsText(e.target.value)}
            placeholder={'["AI","Python","LangGraph","React","TypeScript"]'}
            style={{ fontFamily: "var(--mono)", fontSize: 12.5 }}
          />
        </div>
        <div className="field">
          <label>大灣區青年就業計劃資格（gov.hk 職位）</label>
          <div className="check-row">
            <label>
              <input
                type="checkbox"
                checked={p.gba_age_under_29}
                onChange={(e) => set("gba_age_under_29", e.target.checked)}
              />
              29 歲或以下
            </label>
            <label>
              <input
                type="checkbox"
                checked={p.gba_edu_associate_degree}
                onChange={(e) => set("gba_edu_associate_degree", e.target.checked)}
              />
              副學位或以上學歷
            </label>
          </div>
        </div>
        <button className="btn primary" onClick={save}>
          儲存設定
        </button>

        <div className="section">
          <h4>需要設定嘅嘢（.env / 首次設定）</h4>
          <div className="jd-body" style={{ fontSize: 12.5 }}>
            · LLM API key：喺 <b>.env</b> 填 <b>LLM_API_KEY</b>（DeepSeek）或 <b>LLM_FALLBACK_API_KEY</b>（Qwen）
            <br />· CV 路徑：喺上邊填，或者 .env 嘅 <b>CV_EN_PATH / CV_ZH_PATH</b>
            <br />· 關鍵字：.env 嘅 <b>JOB_KEYWORDS</b>（逗號分隔）
            <br />· 第一次用 JobsDB / OfferToday：撳「立即掃描」時會開個瀏覽器視窗，喺入面登入一次，之後 session 會記住
          </div>
        </div>
      </div>
    </>
  );
}
