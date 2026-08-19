# CV Submit Agent · 求職運作台

本地運行嘅 AI 求職 Agent：自動掃描 **JobsDB、OfferToday、jobs.gov.hk（大灣區青年就業計劃）** 三個平台嘅新職位，每份工自動生成跟 JD 語言嘅 Cover Letter，半自動準備好申請（平台內預填表單 / gov.hk 用 macOS Mail 開好 Email），你 review 完自己撳提交，投完自動記錄入 Dashboard 追蹤。

目標：**每週穩定投遞 15+ 份申請**。

## 功能

- 🕷️ **三個平台監控**：JobsDB（SEEK 登入 session 持久化）、OfferToday（infinite scroll）、jobs.gov.hk GBA 空缺（server-rendered，自動抽申請 email + 聯絡人）
- ✍️ **Cover Letter 自動生成**：跟 JD 語言（英文 JD → 英文 CL；中文 JD → 繁中 CL），只可用 CV 事實（唔吹噓），UI 可編輯，每次儲存開新版本（歷史保留）
- 🎯 **Match Score 過濾**：LLM 評分 0–100 + 理由；低分預設隱藏
- 🖥️ **半自動申請**：agent 開定申請頁並預填（JobsDB / OfferToday），gov.hk 用 AppleScript 開 macOS Mail（收件人＝JD 聯絡 email、主旨、內文、CV 附件），你 check 完自己撳提交/發送——**agent 永遠唔會自動提交**
- 📊 **Dashboard**：狀態看板、投遞歷史檔案庫（JD 快照 + CL 存檔）、統計圖表、面試進度追蹤
- 📧 **外部申請網站**（Greenhouse/Workday/公司官網）：記錄 + 俾 link，唔自動化

## 快速開始

```bash
./run.sh
```

1. 首次會裝 Python venv、Playwright Chromium、前端依賴。
2. 將 `.env` 填好：`LLM_API_KEY`（DeepSeek）、`CV_EN_PATH` / `CV_ZH_PATH`（兩份 CV PDF 絕對路徑）。
3. 開 http://127.0.0.1:8000 → 設定頁填姓名/email/技能清單。
4. 撳「立即掃描」——第一次會開個瀏覽器視窗，喺入面登入 JobsDB / OfferToday 一次（之後 session 會記住）。
5. 掃完喺職位台揀工 → 睇 JD + CL → 撳「開始申請」/「✉ Email 申請」→ 喺開咗嘅視窗/郵件 review 後提交 → 返嚟撳「✔ 記錄已投遞」。

## 開發

```bash
# backend（API :8000）
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --port 8000

# frontend（Vite :5173，proxy 去 8000）
cd frontend && npm run dev
```

測試：

```bash
.venv/bin/pytest tests/ -v
```

## 架構

```
backend/app/
  main.py            FastAPI entry + APScheduler（每 SCAN_INTERVAL_HOURS 自動掃）
  config.py          .env 設定（pydantic-settings）
  models.py          SQLite：profiles / job_applications / cover_letters
  routers/           profile · jobs · scan · stats
  services/
    scraper_govhk.py     jobs.gov.hk（列表 + jobCard 詳情，抽 email/聯絡人）
    scraper_jobsdb.py    JobsDB（SEEK data-automation selectors）
    scraper_offertoday.py OfferToday（分類頁 + infinite scroll）
    matcher.py           keyword pre-score + LLM match score（fallback）
    cl_generator.py      CL 生成（CV 事實硬限制）
    apply_bot.py         平台內半自動預填（唔提交）
    email_bot.py         gov.hk email 申請（AppleScript 開 Mail + fallback）
    scanner.py           scan pipeline（刮 → 去重 → 詳情 → match → CL）
frontend/            React + Vite + TS（build 入 backend/static）
data/                sqlite.db + Playwright profiles（gitignored）
```

## 資料

- `job_applications` 用 `(platform, job_id_on_platform)` 唯一約束，保證唔重複投同一份工
- `jd_text` 存全快照（職位下架都有得睇返）
- `cover_letters` 每次編輯 = 新 version

## Phase 2（未做）

GraphRAG 技能網絡、Text2SQL 查詢、三層記憶＋艾賓浩斯衰減、Qwen3-8B 微調、勞工處 account 網上應徵自動化、雲端部署、通知。
