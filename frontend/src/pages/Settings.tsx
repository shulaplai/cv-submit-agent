import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { api } from "../api";
import type { CVKind } from "../api";
import type { Profile } from "../types";

export function Settings({ pushToast }: { pushToast: (text: string, kind?: "info" | "ok" | "err") => void }) {
  const [p, setP] = useState<Profile | null>(null);
  const [skillsText, setSkillsText] = useState("");
  const [llmTest, setLlmTest] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [browserOk, setBrowserOk] = useState(false);
  const [chromeRunning, setChromeRunning] = useState(false);
  const [browserNote, setBrowserNote] = useState("檢查 Chrome 連線中…");
  const [mailNote, setMailNote] = useState<string | null>(null);
  const [mailOk, setMailOk] = useState(false);
  const [smtpNote, setSmtpNote] = useState<string | null>(null);
  const [smtpOk, setSmtpOk] = useState(false);
  const cvInputs = useRef<Record<string, HTMLInputElement | null>>({
    en: null,
    zh: null,
    ai_en: null,
    ai_zh: null,
    fullstack_en: null,
    fullstack_zh: null,
    developer_en: null,
    developer_zh: null,
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

  const fileRef = (kind: CVKind) => cvInputs.current[kind];

  const cvKindLabel = (kind: CVKind): string => {
    const lang = kind.endsWith("_en") ? "英文" : kind.endsWith("_zh") ? "中文" : kind === "en" ? "英文" : "中文";
    const variant = kind.startsWith("ai")
      ? "AI 版"
      : kind.startsWith("fullstack")
        ? "Full-stack 版"
        : kind.startsWith("developer")
          ? "Developer 版"
          : "通用版";
    return `${lang} ${variant}`;
  };

  const pickCV = async (kind: CVKind, e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      const updated = await api.uploadCV(kind, file);
      setP(updated);
      setSkillsText(updated.skills_json);
      pushToast(`${cvKindLabel(kind)} CV 已上傳（data/cvs/）。`, "ok");
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

  const set = (k: keyof Profile, v: string | boolean | number) => setP({ ...p, [k]: v as never });

  const save = async () => {
    try {
      const updated = await api.saveProfile({
        name: p.name,
        email: p.email,
        cv_en_path: p.cv_en_path,
        cv_zh_path: p.cv_zh_path,
        cv_ai_en_path: p.cv_ai_en_path,
        cv_ai_zh_path: p.cv_ai_zh_path,
        cv_fullstack_en_path: p.cv_fullstack_en_path,
        cv_fullstack_zh_path: p.cv_fullstack_zh_path,
        cv_developer_en_path: p.cv_developer_en_path,
        cv_developer_zh_path: p.cv_developer_zh_path,
        skills_json: skillsText,
        gba_age_under_29: p.gba_age_under_29,
        gba_edu_associate_degree: p.gba_edu_associate_degree,
        llm_api_key: p.llm_api_key,
        llm_fallback_api_key: p.llm_fallback_api_key,
        auto_submit: p.auto_submit,
        intro_en: p.intro_en,
        intro_zh: p.intro_zh,
        offertoday_cv_ai_keyword: p.offertoday_cv_ai_keyword,
        offertoday_cv_it_keyword: p.offertoday_cv_it_keyword,
        offertoday_cv_general_zh_keyword: p.offertoday_cv_general_zh_keyword,
        offertoday_cv_general_en_keyword: p.offertoday_cv_general_en_keyword,
        after_cv_intro_it_zh: p.after_cv_intro_it_zh,
        after_cv_intro_it_en: p.after_cv_intro_it_en,
        after_cv_intro_general_zh: p.after_cv_intro_general_zh,
        after_cv_intro_general_en: p.after_cv_intro_general_en,
        after_cv_intro_ai_zh: p.after_cv_intro_ai_zh,
        after_cv_intro_ai_en: p.after_cv_intro_ai_en,
        cv_ai_title_keywords: p.cv_ai_title_keywords,
        it_keywords: p.it_keywords,
        it_track_enabled: p.it_track_enabled,
        general_track_enabled: p.general_track_enabled,
        general_job_keywords: p.general_job_keywords,
        non_it_keywords: p.non_it_keywords,
        general_wanted_locations: p.general_wanted_locations,
        offertoday_general_search_terms: p.offertoday_general_search_terms,
        offertoday_it_search_terms: p.offertoday_it_search_terms,
        govhk_it_max_jobs: p.govhk_it_max_jobs,
        govhk_general_max_jobs: p.govhk_general_max_jobs,
        offertoday_it_max_per_search: p.offertoday_it_max_per_search,
        offertoday_general_max_per_search: p.offertoday_general_max_per_search,
        // 掃描量
        offertoday_it_max_searches: p.offertoday_it_max_searches,
        offertoday_general_max_searches: p.offertoday_general_max_searches,
        max_scan_jobs: p.max_scan_jobs,
        // 高分豁免上限
        cap_bypass_enabled: p.cap_bypass_enabled,
        cap_bypass_min_score: p.cap_bypass_min_score,
        priority_keywords: p.priority_keywords,
        priority_extra_max: p.priority_extra_max,
        // LLM 預算
        max_enrich_per_scan: p.max_enrich_per_scan,
        enrich_all_it: p.enrich_all_it,
        max_enrich_it_per_scan: p.max_enrich_it_per_scan,
        enrich_general_jobs: p.enrich_general_jobs,
        // AI 搜尋組
        ai_search_terms: p.ai_search_terms,
        ai_search_max_searches: p.ai_search_max_searches,
        ai_search_max_age_days: p.ai_search_max_age_days,
        ai_stale_action: p.ai_stale_action,
        it_blocked_keywords: p.it_blocked_keywords,
        send_method: p.send_method,
        smtp_host: p.smtp_host,
        smtp_port: p.smtp_port,
        smtp_user: p.smtp_user,
        smtp_password: p.smtp_password,
        smtp_from_name: p.smtp_from_name,
        smtp_from_email: p.smtp_from_email,
        smtp_use_ssl: p.smtp_use_ssl,
        smtp_bcc_self: p.smtp_bcc_self,
        ai_check_enabled: p.ai_check_enabled,
        ai_check_batch_size: p.ai_check_batch_size,
        ai_check_limit: p.ai_check_limit,
        ai_check_after_scan: p.ai_check_after_scan,
        // 節奏
        scan_job_delay_min_seconds: p.scan_job_delay_min_seconds,
        scan_job_delay_max_seconds: p.scan_job_delay_max_seconds,
        scan_hour: p.scan_hour,
        scan_day_interval: p.scan_day_interval,
        // 求職者資歷
        years_experience: p.years_experience,
        prefer_ai: p.prefer_ai,
        avoid_contract: p.avoid_contract,
        avoid_agency: p.avoid_agency,
        // 發送前 AI 潤色
        email_polish_enabled: p.email_polish_enabled,
        email_polish_instructions: p.email_polish_instructions,
        intro_polish_enabled: p.intro_polish_enabled,
        intro_polish_instructions: p.intro_polish_instructions,
      });
      setP(updated);
      setSkillsText(updated.skills_json);
      pushToast("設定已儲存。", "ok");
    } catch (e) {
      pushToast(`儲存失敗: ${(e as Error).message}`, "err");
    }
  };

  const testSmtp = async () => {
    setBusy(true);
    setSmtpNote("連線測試中…");
    try {
      const r = await api.testSmtp();
      setSmtpOk(r.ok);
      setSmtpNote(r.note);
    } catch (e) {
      setSmtpOk(false);
      setSmtpNote(`測試失敗: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const sendTestEmail = async () => {
    setBusy(true);
    setSmtpNote("寄測試信中…");
    try {
      const r = await api.sendTestEmail();
      setSmtpOk(r.ok);
      setSmtpNote(`${r.note}（收件人：${r.to}）`);
    } catch (e) {
      setSmtpOk(false);
      setSmtpNote(`測試失敗: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const checkMail = async (deep = false) => {
    setBusy(true);
    setMailNote(deep ? "開一封測試 draft 檢查中…（唔會寄出）" : "檢查中…");
    try {
      if (deep) {
        const r = await api.mailSelftest();
        setMailOk(r.ok);
        setMailNote(r.ok ? r.note : `${r.note}\n原始錯誤：${r.error}\n${r.hint}`);
      } else {
        const r = await api.mailStatus();
        setMailOk(r.ok);
        setMailNote(r.ok ? r.note : `${r.note}\n${r.hint}`);
      }
    } catch (e) {
      setMailOk(false);
      setMailNote(`檢查失敗: ${(e as Error).message}`);
    } finally {
      setBusy(false);
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

  const genAfterCvIntro = async (lang: "zh" | "en", topic: "ai" | "it" | "general") => {
    setBusy(true);
    try {
      const r = await api.generateAfterCvIntro(lang, topic);
      const key = `after_cv_intro_${topic}_${lang}` as keyof Profile;
      set(key, r.text);
      const label = topic === "ai" ? "AI Agent 版" : topic === "it" ? "IT版" : "一般版";
      pushToast(`已生成 ${label} ${lang === "zh" ? "中文" : "English"} 自我介紹。`, "ok");
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
        <div className="section">
          <h4>OfferToday：已上傳 CV 檔名關鍵字（4 類）</h4>
          <div className="note-inline" style={{ borderStyle: "solid" }}>
            自動投遞會喺 OfferToday 嘅「揀履歷」對話框，按已上載 CV 嘅<b>檔名</b>揀啱嘅版本。
            填你檔名入面獨有嘅字（不分大小寫，中英文都搜）。
            <br />
            次序：<b>AI 職位</b>（職位標題有 AI 字眼）→ A.I. 關鍵字 → I.T. 關鍵字 → 一般（跟 JD 語言）；
            <b>其他 IT 工</b> → I.T. 關鍵字 → 一般；<b>其他工</b> → 一般（跟 JD 語言）。
            留空就跳過嗰一級，全部冇填就沿用內建嘅自動判斷。
          </div>
          <div className="field">
            <label>A.I. 關鍵字（AI 版 CV 檔名，中英文通用）</label>
            <input
              value={p.offertoday_cv_ai_keyword}
              onChange={(e) => set("offertoday_cv_ai_keyword", e.target.value)}
              placeholder="例如：AI（檔名 例如 LaiShuLap_AI.pdf）"
            />
          </div>
          <div className="field">
            <label>I.T. 關鍵字（IT 版 CV 檔名，中英文通用；只一份 IT 版）</label>
            <input
              value={p.offertoday_cv_it_keyword}
              onChange={(e) => set("offertoday_cv_it_keyword", e.target.value)}
              placeholder="例如：IT（檔名 例如 LaiShuLap_IT.pdf）"
            />
          </div>
          <div className="field">
            <label>一般中文關鍵字（中文 JD 嘅一般工用）</label>
            <input
              value={p.offertoday_cv_general_zh_keyword}
              onChange={(e) => set("offertoday_cv_general_zh_keyword", e.target.value)}
              placeholder="例如：中文 或 zh（留空 = 自動判斷：含 zh/chinese/中文 就當中文）"
            />
          </div>
          <div className="field">
            <label>一般英文關鍵字（英文 JD 嘅一般工用）</label>
            <input
              value={p.offertoday_cv_general_en_keyword}
              onChange={(e) => set("offertoday_cv_general_en_keyword", e.target.value)}
              placeholder="例如：English 或 en（留空 = 自動判斷：唔含中文標記就當英文）"
            />
          </div>
        </div>
        <div className="section">
          <h4>發送 CV 後嘅自我介紹（OfferToday 自動投遞用，約 100 字）</h4>
          <div className="note-inline" style={{ borderStyle: "solid" }}>
            自動投遞會先發 CV，再自動打一段自我介紹送出。次序同 CV 版本一致：
            <b>職位標題有 AI 字眼 → AI Agent 版</b>；AI 版留空會退回 IT 版；
            其他 IT／程式工用「IT 版」；其餘用「一般版」。語言跟 JD（英文 JD 用英文，中文 JD 用中文）。
            留空會由 AI 即場生成，或撳下邊「AI 生成」預先生成好。
          </div>
          <div className="field">
            <label>AI 職位判斷字眼（逗號分隔，只睇職位標題；留空 = .env 預設）</label>
            <input
              value={p.cv_ai_title_keywords}
              onChange={(e) => set("cv_ai_title_keywords", e.target.value)}
              placeholder="ai,agent"
            />
            <div className="note-inline">
              標題命中呢啲字眼 = 「AI 相關」：會用「AI 版 CV」＋「AI Agent 版自我介紹」，
              而且<b>無視渠道／掃描上限，一定要收</b>（高分豁免）。<br />
              現時預設只有 <b>ai</b> 同 <b>agent</b>（用戶要求：大模型／機器學習等唔再自動算 AI）。
              想加返就喺度填，例如 <b>llm,機器學習</b>。
            </div>
          </div>
          <div className="field">
            <label>AI Agent 版（中文）</label>
            <textarea
              rows={3}
              value={p.after_cv_intro_ai_zh}
              onChange={(e) => set("after_cv_intro_ai_zh", e.target.value)}
              placeholder="你好，我係一位專注 AI Agent 同大型語言模型應用開發嘅工程師…"
            />
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => genAfterCvIntro("zh", "ai")} disabled={busy}>
                ✦ AI 生成 AI Agent 版（中文）
              </button>
            </div>
          </div>
          <div className="field">
            <label>AI Agent 版（English）</label>
            <textarea
              rows={3}
              value={p.after_cv_intro_ai_en}
              onChange={(e) => set("after_cv_intro_ai_en", e.target.value)}
              placeholder="Hi, I'm an engineer focused on AI agents and LLM applications…"
            />
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => genAfterCvIntro("en", "ai")} disabled={busy}>
                ✦ AI 生成 AI Agent 版（English）
              </button>
            </div>
          </div>
          <div className="field">
            <label>IT／程式版（中文）</label>
            <textarea
              rows={3}
              value={p.after_cv_intro_it_zh}
              onChange={(e) => set("after_cv_intro_it_zh", e.target.value)}
              placeholder="你好，我係一位專注 IT 同程式開發嘅工程師…"
            />
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => genAfterCvIntro("zh", "it")} disabled={busy}>
                ✦ AI 生成 IT 版（中文）
              </button>
            </div>
          </div>
          <div className="field">
            <label>IT／程式版（English）</label>
            <textarea
              rows={3}
              value={p.after_cv_intro_it_en}
              onChange={(e) => set("after_cv_intro_it_en", e.target.value)}
              placeholder="Hi, I'm a software engineer focused on IT…"
            />
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => genAfterCvIntro("en", "it")} disabled={busy}>
                ✦ AI 生成 IT 版（English）
              </button>
            </div>
          </div>
          <div className="field">
            <label>一般版（中文）</label>
            <textarea
              rows={3}
              value={p.after_cv_intro_general_zh}
              onChange={(e) => set("after_cv_intro_general_zh", e.target.value)}
              placeholder="你好，我係一位工作認真、學習能力強嘅求職者…"
            />
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => genAfterCvIntro("zh", "general")} disabled={busy}>
                ✦ AI 生成一般版（中文）
              </button>
            </div>
          </div>
          <div className="field">
            <label>一般版（English）</label>
            <textarea
              rows={3}
              value={p.after_cv_intro_general_en}
              onChange={(e) => set("after_cv_intro_general_en", e.target.value)}
              placeholder="Hi, I'm a diligent and quick-learning candidate…"
            />
            <div className="btnrow" style={{ marginTop: 8 }}>
              <button className="btn" onClick={() => genAfterCvIntro("en", "general")} disabled={busy}>
                ✦ AI 生成一般版（English）
              </button>
            </div>
          </div>
        </div>
        {!(p.cv_ai_en_path || p.cv_ai_zh_path || p.cv_fullstack_en_path
           || p.cv_fullstack_zh_path || p.cv_developer_en_path || p.cv_developer_zh_path) && (
          <div className="note-inline err" style={{ borderStyle: "solid" }}>
            ⚠ 未上載 <b>AI 版／Full-stack 版／Developer 版</b> CV —— 所以所有申請（包括 AI 職位）
            都會交<b>通用版 CV</b>。想 AI 職位交針對性版本，喺上面「CV 版本」上載
            AI／Full-stack／Developer 版（檔名例如 <b>..._AI.pdf</b>、<b>..._FullStack.pdf</b>）。
          </div>
        )}

        <div className="section">
          <h4>掃描與分類（IT / 一般職位）</h4>
          <div className="note-inline" style={{ borderStyle: "solid" }}>
            職位分兩個 track：<b>IT 職位</b>（AI／程式／技術）同<b>一般職位</b>（文職、行政、客戶服務等）。
            側邊欄「立即掃描」會掃啟用咗嘅 track；職位台分「IT 職位」同「一般職位」兩頁，側邊欄亦可以揀淨掃其中一邊。
          </div>
          <div className="check-row" style={{ marginTop: 8 }}>
            <label>
              <input
                type="checkbox"
                checked={p.it_track_enabled}
                onChange={(e) => set("it_track_enabled", e.target.checked)}
              />
              啟用 IT 職位掃描
            </label>
            <label>
              <input
                type="checkbox"
                checked={p.general_track_enabled}
                onChange={(e) => set("general_track_enabled", e.target.checked)}
              />
              啟用一般職位掃描
            </label>
          </div>
          <div className="field">
            <label>IT 職位關鍵字（分類 + 過濾；亦用嚟分「IT 版」定「一般版」自我介紹）</label>
            <textarea
              rows={2}
              value={p.it_keywords}
              onChange={(e) => set("it_keywords", e.target.value)}
              placeholder="ai, developer, engineer, software, python, 程式, 工程師, 資訊科技…"
            />
            <div className="note-inline">
              留空用預設（已含 AI、developer、工程師、軟件 等；預設同你填嘅關鍵字會合併，唔會收窄）。
            </div>
          </div>
          <div className="field">
            <label>一般職位關鍵字（掃到嘅非 IT 工要匹配呢啲先會保留；留空用預設）</label>
            <textarea
              rows={2}
              value={p.general_job_keywords}
              onChange={(e) => set("general_job_keywords", e.target.value)}
              placeholder="文員, 行政助理, 客戶服務, 會計, clerk, admin, assistant…"
            />
            <div className="note-inline">
              IT 分類命中嘅工（如 Software Engineer）永遠唔會入一般頁。
            </div>
          </div>
          <div className="field">
            <label>🚫 IT 軌封鎖字眼（保險／地產／sales agent 類；留空 = 內建兩層清單）</label>
            <input
              value={p.it_blocked_keywords}
              onChange={(e) => set("it_blocked_keywords", e.target.value)}
              placeholder="（留空 = 內建：代理, 經紀, 跑數, 保險, 地產, 物業…）"
            />
            <div className="note-inline">
              命中就一定唔入 IT 軌（<b>絕對否決</b>，連 AI 工都唔例外）。留空 = 用內建兩層清單：
              <br />· <b>硬封鎖</b>：代理／經紀／營業員／佣金／跑數／門市／insurance agent／property agent…
              <br />· <b>行業字眼</b>（保險／地產／物業／前線…）：只有標題<b>冇</b>技術訊號（AI／developer／系統／程式／數據…）才封鎖
              —— 所以「AI Engineer（大型保險公司）」會保留、「物業工程師」照封。
              <br />你一旦填咗，就完全用你嗰套（硬封鎖）。
            </div>
          </div>
          <div className="field">
            <label>「唔當 IT」字眼（逗號分隔；留空 = 內建）</label>
            <input
              value={p.non_it_keywords}
              onChange={(e) => set("non_it_keywords", e.target.value)}
              placeholder="mechanical, civil, 土木, 結構, 機械, 排版, 外勤…"
            />
            <div className="note-inline">
              職位標題有呢啲字就唔會入 IT 軌（擋住 engineer／工程師／技術員 等過闊字造成嘅誤分類）；
              但如果同時有「肯定係 IT」字眼（AI、developer、軟件、系統、資訊…）就照當 IT。
            </div>
          </div>
          <div className="field">
            <label>一般工：想去嘅地點（逗號分隔；留空 = 唔篩地點）</label>
            <input
              value={p.general_wanted_locations}
              onChange={(e) => set("general_wanted_locations", e.target.value)}
              placeholder="觀塘, 旺角, 尖沙咀, 中環, 九龍灣, 新蒲崗, 荔枝角, 柴灣, 荃灣, 黃竹坑, 鰂魚涌"
            />
            <div className="note-inline">
              只收工地點喺呢個名單嘅<b>一般</b>工：工地點、標題或者 JD 內文任何一處
              寫住名單內嘅地區就收；寫唔明（例如只寫「港九新界」）或者冇寫地點就唔收，
              所以機場／赤鱲角等唔想去嘅地方自然唔會出現。
              <br />
              IT 工同<b>大灣區計劃</b>（深圳／廣州等）完全唔受影響；舊記錄同已申請嘅工一律唔會動。
            </div>
          </div>
          <div className="field">
            <label>每次掃描上限（每 track / 每個來源；填 0 = 還原 .env 預設）</label>
            <div className="cap-grid">
              <label>
                GovHK 資訊及科技界
                <input
                  type="number"
                  min={0}
                  value={p.govhk_it_max_jobs}
                  onChange={(e) => set("govhk_it_max_jobs", Number(e.target.value))}
                />
              </label>
              <label>
                GovHK 一般職位
                <input
                  type="number"
                  min={0}
                  value={p.govhk_general_max_jobs}
                  onChange={(e) => set("govhk_general_max_jobs", Number(e.target.value))}
                />
              </label>
              <label>
                OfferToday IT（每個分類頁）
                <input
                  type="number"
                  min={0}
                  value={p.offertoday_it_max_per_search}
                  onChange={(e) => set("offertoday_it_max_per_search", Number(e.target.value))}
                />
              </label>
              <label>
                OfferToday 一般（每個關鍵字搜尋）
                <input
                  type="number"
                  min={0}
                  value={p.offertoday_general_max_per_search}
                  onChange={(e) => set("offertoday_general_max_per_search", Number(e.target.value))}
                />
              </label>
            </div>
          </div>
          <div className="field">
            <label>OfferToday IT 職位搜尋字詞（逗號分隔；留空 = 用 .env 預設）</label>
            <input
              value={p.offertoday_it_search_terms}
              onChange={(e) => set("offertoday_it_search_terms", e.target.value)}
              placeholder="developer, FDE, programmer…（AI／agent 放下面「AI 搜尋組」）"
            />
            <div className="note-inline">
              喺 OfferToday 三個技術分類頁（資訊科技／工程師／科技）之上，再逐個字詞開搜尋頁。
              用高精度字詞（例如 <b>developer, FDE, programmer</b>）比用「工程師」雜訊少好多。
              每次最多開幾多個搜尋頁見上面「掃描量」。
            </div>
          </div>
          <div className="field">
            <label>OfferToday 一般職位搜尋字詞（逗號分隔；留空 = 用上面嘅一般關鍵字）</label>
            <input
              value={p.offertoday_general_search_terms}
              onChange={(e) => set("offertoday_general_search_terms", e.target.value)}
              placeholder="文員, 行政助理, 客戶服務…"
            />
            <div className="note-inline">每個字詞會開一個搜尋頁，每次最多 8 個。</div>
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
          <label>CV 版本（申請時自動揀）</label>
          <div className="note-inline" style={{ marginBottom: 6 }}>
            AI 職位 → <b>AI 版</b>；冇 AI 版 → <b>Full-stack 版</b>；連 Full-stack 都冇 →{" "}
            <b>Developer 版</b>；全部都冇 → 上面嘅通用版。OfferToday 會喺「揀履歷」嗰步按檔名揀同一版本。
          </div>
          {(
            [
              ["ai_en", "AI 版（英文）"],
              ["ai_zh", "AI 版（中文）"],
              ["fullstack_en", "Full-stack 版（英文）"],
              ["fullstack_zh", "Full-stack 版（中文）"],
              ["developer_en", "Developer 版（英文）"],
              ["developer_zh", "Developer 版（中文）"],
            ] as [CVKind, string][]
          ).map(([kind, label]) => {
            const pathKey = `cv_${kind}_path` as keyof Profile;
            const current = String(p[pathKey] || "");
            return (
              <div className="cv-variant-row" key={kind}>
                <span className="cv-variant-label">{label}</span>
                <button className="btn" onClick={() => fileRef(kind)?.click()} disabled={busy}>
                  📁 揀檔案…
                </button>
                <span className="cv-variant-path">{current ? current.split("/").pop() : "未設定"}</span>
                {current && (
                  <button
                    className="chip-btn"
                    onClick={() => set(pathKey, "")}
                    title="清除呢個版本（申請時會跳去下一個版本）"
                  >
                    ✕
                  </button>
                )}
                <input
                  type="file"
                  accept=".pdf"
                  ref={(el) => (cvInputs.current[kind] = el)}
                  style={{ display: "none" }}
                  onChange={(e) => pickCV(kind, e)}
                />
              </div>
            );
          })}
          <div className="note-inline" style={{ marginTop: 6 }}>
            記住撳下面「儲存」先會生效。
          </div>
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
          <h4>掃描量（收幾多工 — 可以加大／唔設限）</h4>
          <div className="note-inline" style={{ borderStyle: "solid" }}>
            每個渠道每次掃描收幾多份。數值 <b>0 = 唔設限</b>（用內建硬上限：gov.hk 30 頁／OfferToday
            每頁 12 次捲動）。撳 <b>+10 / +50</b> 快速加大；改完記得撳下面「儲存設定」。
          </div>
          {(
            [
              ["govhk_it_max_jobs", "GovHK·資訊及科技界（每次）"],
              ["govhk_general_max_jobs", "GovHK·一般職位（每次）"],
              ["offertoday_it_max_per_search", "OfferToday IT（每個搜尋頁）"],
              ["offertoday_general_max_per_search", "OfferToday 一般（每個搜尋頁）"],
              ["offertoday_it_max_searches", "OfferToday IT 額外搜尋字詞數"],
              ["offertoday_general_max_searches", "OfferToday 一般搜尋字詞數"],
              ["max_scan_jobs", "每個 track 每次掃描總上限（0 = 不限）"],
            ] as const
          ).map(([key, label]) => (
            <div className="field" key={key}>
              <label>{label}</label>
              <div className="btnrow" style={{ alignItems: "center" }}>
                <input
                  type="number"
                  min={0}
                  max={1000}
                  className="num-input"
                  style={{ width: 110 }}
                  value={p[key] as number}
                  onChange={(e) => set(key, Number(e.target.value))}
                />
                <button className="chip-btn" onClick={() => set(key, (p[key] as number) + 10)}>
                  +10
                </button>
                <button className="chip-btn" onClick={() => set(key, (p[key] as number) + 50)}>
                  +50
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="section">
          <h4>高分豁免上限（評級高嘅工無視上限照收）</h4>
          <label className="check-row" style={{ alignItems: "flex-start" }}>
            <input
              type="checkbox"
              style={{ marginTop: 4 }}
              checked={p.cap_bypass_enabled}
              onChange={(e) => set("cap_bypass_enabled", e.target.checked)}
            />
            <span>
              <b>開啟</b> — 符合下面條件嘅工唔計入渠道上限，照樣入庫（每渠道另有豁免額）。
              <br />
              <span style={{ fontSize: 12, color: "var(--ink-soft)" }}>
                條件：職位標題命中「優先字詞」（預設 = AI 職位關鍵字，即係 AI／人工智能／機器學習／
                LLM／大模型…），或者關鍵字重疊分數達到門檻分。
              </span>
            </span>
          </label>
          <div className="field">
            <label>豁免門檻分（0-100；關鍵字分數達到就當高分）</label>
            <input
              type="number"
              min={0}
              max={100}
              className="num-input"
              value={p.cap_bypass_min_score}
              onChange={(e) => set("cap_bypass_min_score", Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label>優先字詞（逗號分隔；留空 = 只用 AI 職位關鍵字）</label>
            <textarea
              rows={2}
              value={p.priority_keywords}
              onChange={(e) => set("priority_keywords", e.target.value)}
              placeholder="full stack, react, laravel, python, 全端…"
            />
          </div>
          <div className="field">
            <label>每個渠道每次掃描最多豁免幾多份（防失控）</label>
            <input
              type="number"
              min={0}
              max={300}
              className="num-input"
              value={p.priority_extra_max}
              onChange={(e) => set("priority_extra_max", Number(e.target.value))}
            />
            <div className="note-inline">
              掃描完成後，側邊欄會顯示「豁免收多咗 N 份」。
            </div>
          </div>
        </div>

        <div className="section">
          <h4>LLM 預算（每份新 IT 工 ≈ 1 次評分 + 1 次 CL）</h4>
          <div className="field">
            <label>一般工每次掃描最多評分幾多份（0 = 唔評分）</label>
            <input
              type="number"
              min={0}
              max={500}
              className="num-input"
              value={p.max_enrich_per_scan < 0 ? 30 : p.max_enrich_per_scan}
              onChange={(e) => set("max_enrich_per_scan", Number(e.target.value))}
              title="-1 = 跟 .env（預設 30）"
            />
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={p.enrich_all_it}
              onChange={(e) => set("enrich_all_it", e.target.checked)}
            />
            所有新 IT 工都完整評分（唔限數量）
          </label>
          <div className="field">
            <label>（安全上限）每次掃描最多評分幾多份新 IT 工（0 = 唔限）</label>
            <input
              type="number"
              min={0}
              max={500}
              className="num-input"
              value={p.max_enrich_it_per_scan < 0 ? 0 : p.max_enrich_it_per_scan}
              onChange={(e) => set("max_enrich_it_per_scan", Number(e.target.value))}
              title="-1 = 跟 .env；0 = 唔設上限"
            />
            <div className="note-inline">
              收工量加大之後，掃描時間同 LLM 花費會一齊上升；呢個數可以封頂。
              舊工（未評分嘅）可以用職位台「⇪ 補齊未評分 IT 工」分批補。
            </div>
          </div>
          <label className="check-row" style={{ alignItems: "flex-start" }}>
            <input
              type="checkbox"
              style={{ marginTop: 4 }}
              checked={p.enrich_general_jobs}
              onChange={(e) => set("enrich_general_jobs", e.target.checked)}
            />
            <span>
              <b>一般工都要 LLM 評分</b>（預設<b>唔開</b>）
              <br />
              <span style={{ fontSize: 12, color: "var(--ink-soft)" }}>
                唔開：一般工照樣收、照樣有 JD 同 AI／合約／外派標籤，但<b>唔會洗 LLM</b>
                （唔評分、唔生成 CL、唔寫摘要），卡片會標示「未評分」。
                想評某一兩份，就喺詳情頁撳「↻ 重新整理」。
                <br />
                IT 工永遠都會完整評分（只有 IT 工需要，見上面）。
              </span>
            </span>
          </label>
        </div>

        <div className="section">
          <h4>AI 搜尋組（專門搵 AI／agent 工，限 7 日內）</h4>
          <div className="note-inline" style={{ borderStyle: "solid" }}>
            呢組字詞會<b>獨立開搜尋頁</b>（唔同其他字詞爭預算），而且只收<b>7 日內</b>刊登嘅工：
            一撞到過期就即刻停（可以揀停該渠道，或者暫停成個掃描）。
            字詞命中「AI／agent」（見上邊 AI 職位判斷字眼）嘅工<b>一定要收</b>，唔會被渠道上限擋。
          </div>
          <div className="field">
            <label>AI 搜尋字詞（逗號分隔；留空 = .env 預設 agent,AI）</label>
            <input
              value={p.ai_search_terms}
              onChange={(e) => set("ai_search_terms", e.target.value)}
              placeholder="agent, AI, ai engineer, ai agent"
            />
          </div>
          <div className="field">
            <label>每次掃描最多開幾多個 AI 搜尋頁</label>
            <input
              type="number"
              min={0}
              max={30}
              className="num-input"
              value={p.ai_search_max_searches}
              onChange={(e) => set("ai_search_max_searches", Number(e.target.value))}
            />
          </div>
          <div className="field">
            <label>AI 組刊登日期上限（日；0 = 唔限）</label>
            <input
              type="number"
              min={0}
              max={60}
              className="num-input"
              value={p.ai_search_max_age_days < 0 ? 7 : p.ai_search_max_age_days}
              onChange={(e) => set("ai_search_max_age_days", Number(e.target.value))}
            />
            <div className="note-inline">其他渠道維持原本（大灣區 7 日、其餘 14 日）。</div>
          </div>
          <div className="field">
            <label>AI 組撞到過期工點做</label>
            <select
              className="chip-btn"
              style={{ appearance: "auto" }}
              value={p.ai_stale_action || "channel"}
              onChange={(e) => set("ai_stale_action", e.target.value)}
            >
              <option value="channel">停該渠道，繼續掃其他（預設）</option>
              <option value="scan">暫停成個掃描（即刻收工）</option>
            </select>
          </div>
          <div className="note-inline">
            想排除某啲字眼（例如「保險」「地產」「代理」）可以喺上面「唔當 IT 字眼」加，
            預設唔會過濾（AI／agent 一律照收）。
          </div>
        </div>

        <div className="section">
          <h4>批量 AI 檢查（LLM 搵出「其實唔係 IT」嘅工）</h4>
          <div className="note-inline" style={{ borderStyle: "solid" }}>
            關鍵字規則永遠有漏網之魚（例：「網路銷售代理」有『網路』、非電腦嘅「工程師」）。
            呢個功能會<b>分批</b>（每次 40 份職位 + JD 頭段做一個 LLM call）判斷每份工係唔係真正 IT／AI，
            判「非 IT」就標<b>低匹配</b>（職位台預設隱藏，可以喺「低匹配」chip 睇返 / 撳「↩ 還原 AI 判定」）。
            只檢查 <b>IT 軌</b>、未檢查過、未投遞嘅工 —— 一般工唔會洗錢。
            <br />職位台有「🤖 批量 AI 檢查」掣可以隨時手動跑。
          </div>
          <label className="check-row" style={{ alignItems: "flex-start" }}>
            <input
              type="checkbox"
              style={{ marginTop: 4 }}
              checked={p.ai_check_enabled}
              onChange={(e) => set("ai_check_enabled", e.target.checked)}
            />
            <span>
              <b>啟用 AI 檢查</b>（唔開都仍然可以喺職位台手動撳掣）
            </span>
          </label>
          <label className="check-row" style={{ alignItems: "flex-start" }}>
            <input
              type="checkbox"
              style={{ marginTop: 4 }}
              checked={p.ai_check_after_scan}
              onChange={(e) => set("ai_check_after_scan", e.target.checked)}
            />
            <span>每次掃描完自動跑一次（預設唔開：唔想偷偷洗錢）</span>
          </label>
          <div className="check-row">
            <div className="field" style={{ marginBottom: 0 }}>
              <label>每批幾多份（一個 LLM call）</label>
              <input
                type="number"
                min={1}
                max={200}
                className="num-input"
                value={p.ai_check_batch_size || 40}
                onChange={(e) => set("ai_check_batch_size", Number(e.target.value))}
                title="0 = 跟 .env（預設 40）"
              />
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>每次最多檢查幾多份</label>
              <input
                type="number"
                min={1}
                max={2000}
                className="num-input"
                value={p.ai_check_limit || 200}
                onChange={(e) => set("ai_check_limit", Number(e.target.value))}
                title="0 = 跟 .env（預設 200）"
              />
            </div>
          </div>
          <div className="note-inline">
            成本估算：<b>份數 ÷ 每批份數</b>＝ LLM 次數。例如 200 份、每批 40 → 5 次 call。
          </div>
        </div>

        <div className="section">
          <h4>求職者資歷（影響評分同排序）</h4>
          <div className="field">
            <label>年資（年）— 評分會扣「要 5 年+/senior」嘅工，標示「資歷超出」</label>
            <input
              type="number"
              min={0}
              max={40}
              className="num-input"
              value={p.years_experience}
              onChange={(e) => set("years_experience", Number(e.target.value))}
            />
          </div>
          <div className="check-row" style={{ flexWrap: "wrap" }}>
            <label>
              <input
                type="checkbox"
                checked={p.prefer_ai}
                onChange={(e) => set("prefer_ai", e.target.checked)}
              />
              優先 AI 相關職位
            </label>
            <label>
              <input
                type="checkbox"
                checked={p.avoid_contract}
                onChange={(e) => set("avoid_contract", e.target.checked)}
              />
              想避開合約／臨時工
            </label>
            <label>
              <input
                type="checkbox"
                checked={p.avoid_agency}
                onChange={(e) => set("avoid_agency", e.target.checked)}
              />
              想避開外派／獵頭
            </label>
          </div>
          <div className="note-inline">
            呢啲設定會寫入 LLM 評分提示（合約／外派扣分、超出年資扣分），
            職位台亦可以一撳篩走（chip：「✕ 合約／臨時」「✕ 外派／獵頭」「✓ 資歷啱」）。
          </div>
        </div>

        <div className="section">
          <h4>發送前 AI 潤色（令封信唔似 AI 拼砌）</h4>
          <label className="check-row" style={{ alignItems: "flex-start" }}>
            <input
              type="checkbox"
              style={{ marginTop: 4 }}
              checked={p.email_polish_enabled}
              onChange={(e) => set("email_polish_enabled", e.target.checked)}
            />
            <span>
              <b>Email 申請</b>（gov.hk 等）：寄出前用 AI 潤色<b>整封內文</b>（稱呼同簽名逐字保留，
              唔會加履歷以外嘅事實）。預覽同實寄係同一份文字，同一封信唔會重複洗 LLM。
            </span>
          </label>
          <div className="field">
            <label>Email 潤色額外指示（可以留空）</label>
            <textarea
              rows={2}
              value={p.email_polish_instructions}
              onChange={(e) => set("email_polish_instructions", e.target.value)}
              placeholder="例如：更正式一點、刪走重複句、唔好太長…"
            />
          </div>
          <label className="check-row" style={{ alignItems: "flex-start" }}>
            <input
              type="checkbox"
              style={{ marginTop: 4 }}
              checked={p.intro_polish_enabled}
              onChange={(e) => set("intro_polish_enabled", e.target.checked)}
            />
            <span>
              <b>OfferToday 自我介紹</b>：發完 CV 跟住送出嘅自我介紹，發送前同樣潤色一次
              （保持 80–120 字、唔加事實）。
            </span>
          </label>
          <div className="field">
            <label>自我介紹潤色額外指示（可以留空）</label>
            <textarea
              rows={2}
              value={p.intro_polish_instructions}
              onChange={(e) => set("intro_polish_instructions", e.target.value)}
              placeholder="例如：再簡潔一點、唔好超過 90 字…"
            />
          </div>
          <div className="note-inline">
            潤色失敗（API 出錯／輸出唔合格）一律用原文寄出，唔會漏寄。
          </div>
        </div>

        <div className="section">
          <h4>掃描節奏（太密會被網站擋）</h4>
          <div className="check-row">
            <div className="field" style={{ marginBottom: 0 }}>
              <label>每份工之間最少隔幾秒</label>
              <input
                type="number"
                min={0}
                max={60}
                step={0.5}
                className="num-input"
                value={p.scan_job_delay_min_seconds || 0}
                onChange={(e) => set("scan_job_delay_min_seconds", Number(e.target.value))}
              />
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>最多隔幾秒</label>
              <input
                type="number"
                min={0}
                max={60}
                step={0.5}
                className="num-input"
                value={p.scan_job_delay_max_seconds || 0}
                onChange={(e) => set("scan_job_delay_max_seconds", Number(e.target.value))}
              />
            </div>
          </div>
          <div className="note-inline">
            0 = 跟 .env 預設（4–6 秒隨機，避免被 anti-bot 擋）。收工量加大之後，呢個間隔就係掃描時間嘅主因。
          </div>
          <div className="check-row">
            <div className="field" style={{ marginBottom: 0 }}>
              <label>自動掃描時間（0-23 時）</label>
              <input
                type="number"
                min={0}
                max={23}
                className="num-input"
                value={p.scan_hour < 0 ? 3 : p.scan_hour}
                onChange={(e) => set("scan_hour", Number(e.target.value))}
                title="-1 = 跟 .env（預設 03:00）"
              />
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>幾多日掃一次（0 = 唔自動掃）</label>
              <input
                type="number"
                min={0}
                max={30}
                className="num-input"
                value={p.scan_day_interval < 0 ? 2 : p.scan_day_interval}
                onChange={(e) => set("scan_day_interval", Number(e.target.value))}
                title="-1 = 跟 .env（預設每 2 日）"
              />
            </div>
          </div>
          <div className="note-inline">改完儲存即刻重建排程（唔使改 .env 重啟）。</div>
        </div>

        <div className="section">
          <h4>自動寄 email（SMTP）— 唔需要 macOS Mail 權限</h4>
          <div className="note-inline" style={{ borderStyle: "solid" }}>
            要真正「自己寄出去（內文 + CV 附件自動填好）」而唔想撞 macOS 自動化權限（-10004），
            就用 SMTP：由程式直接連你嘅郵箱寄信，<b>唔會開 Mail、唔需要任何 macOS 權限</b>。
            <br />常見設定：
            <b>Gmail</b> smtp.gmail.com:587（要「應用程式密碼」16 位）／
            <b>iCloud</b> smtp.mail.me.com:587（應用程式專用密碼）／
            <b>Outlook／公司</b> smtp.office365.com:587（可能要管理員開 SMTP AUTH）／
            <b>QQ／163</b> smtp.qq.com:465（開 SSL）。
          </div>
          <div className="field">
            <label>寄信方式</label>
            <select
              className="chip-btn"
              style={{ appearance: "auto" }}
              value={p.send_method || "auto"}
              onChange={(e) => set("send_method", e.target.value)}
            >
              <option value="auto">自動（有填 SMTP 用 SMTP，冇就用 macOS Mail）</option>
              <option value="smtp">只用 SMTP（唔會開 Mail）</option>
              <option value="mail">只用 macOS Mail（AppleScript）</option>
            </select>
          </div>
          <div className="check-row">
            <div className="field" style={{ marginBottom: 0, flex: 2 }}>
              <label>SMTP 伺服器</label>
              <input
                value={p.smtp_host}
                onChange={(e) => set("smtp_host", e.target.value)}
                placeholder="smtp.gmail.com"
              />
            </div>
            <div className="field" style={{ marginBottom: 0, flex: 1 }}>
              <label>Port</label>
              <input
                type="number"
                className="num-input"
                value={p.smtp_port || 587}
                onChange={(e) => set("smtp_port", Number(e.target.value))}
              />
            </div>
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={p.smtp_use_ssl}
              onChange={(e) => set("smtp_use_ssl", e.target.checked)}
            />
            用 SSL（465 通常要開；587 用 STARTTLS 唔開）
          </label>
          <div className="field">
            <label>SMTP 帳號（email）</label>
            <input
              value={p.smtp_user}
              onChange={(e) => set("smtp_user", e.target.value)}
              placeholder="you@gmail.com"
            />
          </div>
          <div className="field">
            <label>SMTP 密碼（Gmail／iCloud 要填應用程式密碼，唔係平時登入密碼）</label>
            <input
              type="password"
              value={p.smtp_password}
              onChange={(e) => set("smtp_password", e.target.value)}
              placeholder="xxxx xxxx xxxx xxxx"
            />
          </div>
          <div className="check-row">
            <div className="field" style={{ marginBottom: 0 }}>
              <label>顯示名（收件人見到嘅寄件人）</label>
              <input
                value={p.smtp_from_name}
                onChange={(e) => set("smtp_from_name", e.target.value)}
                placeholder="Lai Shu Lap"
              />
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>寄件人 email（留空 = SMTP 帳號）</label>
              <input
                value={p.smtp_from_email}
                onChange={(e) => set("smtp_from_email", e.target.value)}
                placeholder="you@gmail.com"
              />
            </div>
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={p.smtp_bcc_self}
              onChange={(e) => set("smtp_bcc_self", e.target.checked)}
            />
            每封申請信 BCC 一份去自己（做備份／追蹤）
          </label>
          <div className="btnrow" style={{ marginTop: 10 }}>
            <button className="btn" onClick={testSmtp} disabled={busy}>
              🔌 測試 SMTP（唔會寄信）
            </button>
            <button className="btn primary" onClick={sendTestEmail} disabled={busy}>
              ✉ 寄測試信去自己
            </button>
          </div>
          {smtpNote && (
            <div
              className={`note-inline ${smtpOk ? "ok" : "err"}`}
              style={{ borderStyle: "solid", whiteSpace: "pre-wrap", marginTop: 8 }}
            >
              {smtpNote}
            </div>
          )}
          <div className="note-inline">
            ⚠ 記得撳最底「儲存設定」先會生效。填好之後，gov.hk email 申請會<b>自動寄出</b>：
            內文（已 AI 潤色）＋ CV 附件（跟職位揀版本）都會自動處理，唔會再彈 mailto。
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
          <div className="field" style={{ marginTop: 14 }}>
            <label>Email 發送權限（macOS Mail 自動化）</label>
            <div className="btnrow">
              <button className="btn" onClick={() => checkMail(false)} disabled={busy}>
                📧 檢查 Mail 權限
              </button>
              <button className="btn primary" onClick={() => checkMail(true)} disabled={busy}>
                🧪 測試開信（唔會寄出）
              </button>
            </div>
            {mailNote && (
              <div
                className={`note-inline ${mailOk ? "ok" : "err"}`}
                style={{ borderStyle: "solid", whiteSpace: "pre-wrap", marginTop: 8 }}
              >
                {mailNote}
              </div>
            )}
            <div className="note-inline">
              gov.hk 嘅 email 申請係用 macOS Mail 嘅 AppleScript 發送。見到
              <b>「-10004 越權取用的錯誤」</b>＝ macOS 未批准「邊個程式叫 Mail」。
              <br />
              ⚠ 記住：<b>「檢查 Mail 權限」唔可以只用版本號判斷</b>（macOS 連未批准都會答版本），
              所以請撳 <b>「🧪 測試開信」</b>—— 佢真係開一封 draft（即刻關閉、唔會寄出），
              失敗就會連原始錯誤一齊顯示。
              <br />
              修法：<b>系統設定 → 隱私權與安全性 → 自動化</b> → 揾<b>終端機／Terminal</b>
              （或你啟動 server 嗰個程式）→ 勾返<b>「郵件 / Mail」</b> → 重啟 server。
              <br />
              最穩做法：由<b>你自己嘅 Terminal</b> 跑 <b>./run.sh</b>（權限跟「邊個 app 叫 Mail」，
              Terminal 通常已經批過）。就算未批，發送失敗都會自動改用
              <b>mailto + 複製內文到剪貼簿</b>，唔會卡死。
            </div>
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
