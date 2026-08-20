import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { api } from "../api";
import type { Profile } from "../types";

export function Settings({ pushToast }: { pushToast: (text: string, kind?: "info" | "ok" | "err") => void }) {
  const [p, setP] = useState<Profile | null>(null);
  const [skillsText, setSkillsText] = useState("");
  const [llmTest, setLlmTest] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [browserOk, setBrowserOk] = useState(false);
  const [chromeRunning, setChromeRunning] = useState(false);
  const [browserNote, setBrowserNote] = useState("檢查 Chrome 連線中…");
  const cvInputs = useRef<{ en: HTMLInputElement | null; zh: HTMLInputElement | null }>({
    en: null,
    zh: null,
  });

  useEffect(() => {
    api.browserStatus().then((s) => {
      setBrowserOk(s.using_real_chrome);
      setChromeRunning(s.chrome_running);
      setBrowserNote(s.note);
    }).catch(() => setBrowserNote("檢查唔到（server 未起？）"));
  }, []);

  const openChrome = async () => {
    setBusy(true);
    try {
      const r = await api.launchChrome();
      setBrowserNote(r.message);
      setBrowserOk(r.ok);
      if (r.ok) pushToast("Chrome 已連上 — 自動投遞會用你登入咗嘅狀態。", "ok");
    } catch (e) {
      setBrowserNote(`失敗: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const restartChrome = async () => {
    setBusy(true);
    setBrowserNote("正在退出 Chrome 並重開（分頁會還原）…");
    try {
      const r = await api.restartChrome();
      setBrowserNote(r.message);
      setBrowserOk(r.ok);
      setChromeRunning(false);
      if (r.ok) pushToast("Chrome 已重開並連上！", "ok");
    } catch (e) {
      setBrowserNote(`失敗: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const fileRef = (kind: "en" | "zh") => cvInputs.current[kind];

  const pickCV = async (kind: "en" | "zh", e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      const updated = await api.uploadCV(kind, file);
      setP(updated);
      setSkillsText(updated.skills_json);
      pushToast(`${kind === "en" ? "英文" : "中文"} CV 已上傳（data/cvs/）。`, "ok");
    } catch (err) {
      pushToast(`上傳失敗: ${(err as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  };

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
        llm_api_key: p.llm_api_key,
        llm_fallback_api_key: p.llm_fallback_api_key,
        auto_submit: p.auto_submit,
        intro_en: p.intro_en,
        intro_zh: p.intro_zh,
      });
      setP(updated);
      setSkillsText(updated.skills_json);
      pushToast("設定已儲存。", "ok");
    } catch (e) {
      pushToast(`儲存失敗: ${(e as Error).message}`, "err");
    }
  };

  const testLLM = async () => {
    setBusy(true);
    setLlmTest(null);
    try {
      const r = await api.testLLM();
      if (r.ok) {
        setLlmTest(`✓ 連線正常（${r.model}，${r.latency_ms}ms）`);
      } else {
        setLlmTest(`✗ ${r.error}`);
      }
    } catch (e) {
      setLlmTest(`✗ ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const extractSkills = async () => {
    setBusy(true);
    try {
      const r = await api.extractSkills();
      if (r.skills.length) {
        setSkillsText(JSON.stringify(r.skills));
        pushToast("已由 CV 抽取技能清單，確認後記得儲存。", "ok");
      } else {
        pushToast("抽唔到技能，可能 CV 冇內容。", "err");
      }
    } catch (e) {
      pushToast(`抽取失敗: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
    }
  };

  const genIntro = async (lang: "zh" | "en") => {
    setBusy(true);
    try {
      const r = await api.generateIntro(lang);
      if (lang === "zh") set("intro_zh", r.text);
      else set("intro_en", r.text);
      pushToast(`${lang === "zh" ? "中文" : "English"}簡介已生成，可以編輯後儲存。`, "ok");
    } catch (e) {
      pushToast(`生成失敗: ${(e as Error).message}`, "err");
    } finally {
      setBusy(false);
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
        <div className="section">
          <h4>LLM（Cover Letter 生成用）</h4>
          <div className="field">
            <label>Primary API Key（DeepSeek）— 喺度填會覆蓋 .env</label>
            <input
              type="password"
              value={p.llm_api_key}
              onChange={(e) => set("llm_api_key", e.target.value)}
              placeholder="sk-…（或者喺 .env 填 LLM_API_KEY）"
            />
          </div>
          <div className="field">
            <label>Fallback API Key（Qwen DashScope，可選）</label>
            <input
              type="password"
              value={p.llm_fallback_api_key}
              onChange={(e) => set("llm_fallback_api_key", e.target.value)}
              placeholder="sk-…（或者喺 .env 填 LLM_FALLBACK_API_KEY）"
            />
          </div>
          <div className="btnrow">
            <button className="btn" onClick={save} disabled={busy}>
              儲存設定
            </button>
            <button className="btn" onClick={testLLM} disabled={busy}>
              測試連線
            </button>
          </div>
          {llmTest && <div className={`note-inline ${llmTest.startsWith("✓") ? "ok" : "err"}`}>{llmTest}</div>}
          <div className="note-inline" style={{ marginTop: 10 }}>
            Key 只存喺本機 SQLite（`data/cvsubmit.db`），唔會送出街。填咗之後唔使改 .env 重啟。
          </div>
        </div>

        <div className="field">
          <label>姓名（Email 申請簽名用）</label>
          <input value={p.name} onChange={(e) => set("name", e.target.value)} placeholder="例如：陳大文" />
        </div>
        <div className="field">
          <label>Email</label>
          <input value={p.email} onChange={(e) => set("email", e.target.value)} placeholder="你嘅聯絡 email" />
        </div>
        <div className="section">
          <h4>自我簡介（申請時自動嵌入）</h4>
          <div className="field">
            <label>中文簡介（中文 JD 嘅工、gov.hk email、OfferToday 訊息用）</label>
            <textarea
              rows={3}
              value={p.intro_zh}
              onChange={(e) => set("intro_zh", e.target.value)}
              placeholder="例如：你好，我係一位專注 AI 同全端開發嘅工程師，有 X 年經驗，熟悉 LangGraph、React、TypeScript，希望有機會加入貴公司。"
            />
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => genIntro("zh")} disabled={busy}>
                ✦ AI 生成中文簡介
              </button>
            </div>
          </div>
          <div className="field">
            <label>English intro（英文 JD 嘅工用）</label>
            <textarea
              rows={3}
              value={p.intro_en}
              onChange={(e) => set("intro_en", e.target.value)}
              placeholder="e.g. Hi, I am a software engineer focused on AI and full-stack development..."
            />
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => genIntro("en")} disabled={busy}>
                ✦ AI 生成 English 簡介
              </button>
            </div>
          </div>
          <div className="note-inline" style={{ borderStyle: "solid" }}>
            呢段簡介會加喺 Cover Letter 前面，一齊成為申請訊息／email 內文。
          </div>
        </div>
        <div className="field">
          <label>英文 CV（PDF）</label>
          <div className="btnrow">
            <button className="btn" onClick={() => fileRef("en")?.click()} disabled={busy}>
              📁 揀檔案（英文 CV）…
            </button>
            <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--ink-soft)", alignSelf: "center" }}>
              {p.cv_en_path ? p.cv_en_path.split("/").pop() : "未設定"}
            </span>
          </div>
          <input
            type="file"
            accept=".pdf"
            ref={(el) => (cvInputs.current.en = el)}
            style={{ display: "none" }}
            onChange={(e) => pickCV("en", e)}
          />
        </div>
        <div className="field">
          <label>中文 CV（PDF）— gov.hk 中文 JD 用呢份</label>
          <div className="btnrow">
            <button className="btn" onClick={() => fileRef("zh")?.click()} disabled={busy}>
              📁 揀檔案（中文 CV）…
            </button>
            <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--ink-soft)", alignSelf: "center" }}>
              {p.cv_zh_path ? p.cv_zh_path.split("/").pop() : "未設定"}
            </span>
          </div>
          <input
            type="file"
            accept=".pdf"
            ref={(el) => (cvInputs.current.zh = el)}
            style={{ display: "none" }}
            onChange={(e) => pickCV("zh", e)}
          />
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
          <div className="btnrow" style={{ marginTop: 8 }}>
            <button className="btn" onClick={extractSkills} disabled={busy}>
              ✦ 由 CV 自動抽取
            </button>
          </div>
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
        <div className="section">
          <h4>自動投遞瀏覽器（JobsDB / OfferToday）</h4>
          <div className={`note-inline ${browserOk ? "ok" : "err"}`} style={{ borderStyle: "solid" }}>
            {browserNote}
          </div>
          {!browserOk && (
            <>
              <div className="btnrow" style={{ marginTop: 10 }}>
                <button className="btn" onClick={openChrome} disabled={busy}>
                  🔗 開啟專用 Chrome
                </button>
                {chromeRunning && (
                  <button className="btn primary" onClick={restartChrome} disabled={busy}>
                    🔁 重啟專用 Chrome
                  </button>
                )}
              </div>
              <div className="note-inline" style={{ marginTop: 8 }}>
                ⚠ Chrome 136+ 官方封咗「用預設 profile 遠端操控」，所以用一個<b>專用 Chrome 視窗</b>（唔會掂你原本 Chrome）。
                第一次開啟後，喺嗰個視窗<b>登入 JobsDB / OfferToday 一次</b>，之後會一直記住。
              </div>
            </>
          )}
          <div className="note-inline" style={{ marginTop: 10 }}>
            未連接時會用後備方案（獨立 Playwright 瀏覽器，登入一次，session 存本機）。
          </div>
        </div>
        <div className="section">
          <h4>申請行為</h4>
          <label className="check-row" style={{ alignItems: "flex-start" }}>
            <input
              type="checkbox"
              checked={p.auto_submit}
              onChange={(e) => set("auto_submit", e.target.checked)}
              style={{ marginTop: 4 }}
            />
            <span>
              <b>自動投遞</b> — 撳「申請」會自己填 CL + 上傳 CV 並直接提交（gov.hk 會自動發 email）。
              <br />
              <span style={{ color: "var(--accent-deep)", fontSize: 12 }}>
                ⚠ 唔會停低等你確認。驗證碼／登入牆／缺 CL／缺 CV ／外部網站會自動煞停（改為手動模式提示）。
              </span>
              <br />
              <span style={{ fontSize: 12, color: "var(--ink-soft)" }}>
                關咗就係半自動：預填後等你喺瀏覽器自己撳提交。每份工喺詳情頁都可以單獨切換「自動／手動」。
              </span>
            </span>
          </label>
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
