# CV Submit Agent · 求職運作台

本地運行嘅 AI 求職 Agent：自動掃描 **OfferToday、jobs.gov.hk** 嘅新職位（JobsDB 暫時隱藏），每份工自動生成跟 JD 語言嘅 Cover Letter，半自動準備好申請（平台內預填表單 / gov.hk 用 macOS Mail 開好 Email），你 review 完自己撳提交，投完自動記錄入 Dashboard 追蹤。

目標：**每週穩定投遞 15+ 份申請**。

## 功能

- 🕷️ **雙軌掃描（IT / 一般職位）**：每份工分類入 **IT**（AI／程式／技術）或 **一般**（文職、行政、客戶服務等非 IT）track，職位台分「**IT 職位**」同「**一般職位**」兩頁顯示。側邊欄「立即掃描」預設掃晒兩個 track（設定頁可逐個開關），亦可以揀「**淨掃描：IT**」或「**淨掃描：一般**」。每次掃描喺側邊欄分開顯示兩個 track 嘅結果（新/掃到/過期）
  - **IT track**：OfferToday 三個技術分類（資訊科技／工程師／科技，每分類每次上限可設）、gov.hk **大灣區青年就業計劃**（`govhk_gbayes`，刊登日期限 30 日）＋**資訊及科技界**（`govhk_it`，每次上限可設）
  - **一般 track**：OfferToday 關鍵字搜尋（`<關鍵字>-jobs`，每個字詞一個搜尋頁）、gov.hk **主 quickview**（`govhk_general`，全部職位類別，由新到舊，過濾一般關鍵字）
  - **每 2 日凌晨 03:00（香港時間）自動掃描**（`.env` 可改：`SCAN_HOUR` / `SCAN_DAY_INTERVAL`），起機唔會自動補掃；掃描期間每份工（攞 JD／LLM 評分）之間**隨機隔 4–6 秒**（`SCAN_JOB_DELAY_MIN_SECONDS` / `SCAN_JOB_DELAY_MAX_SECONDS`），避免太快被 OfferToday 等網站擋
- 📅 **OfferToday 都有刊登日期喇**：OfferToday 詳情頁內嵌 JSON-LD（`datePosted`），`fetch_detail` 會抽返出嚟做刊登日期，過期（超過期限）嘅 OfferToday 工一樣會喺補齊/刷新後被過濾
- ✍️ **Cover Letter 自動生成**：跟 JD 語言（英文 JD → 英文 CL；中文 JD → 繁中 CL），只可用 CV 事實（唔吹噓），UI 可編輯，每次儲存開新版本（歷史保留）；生成後自動驗證語言/長度，唔啱會重試一次
- 📄 **全部工都會有 JD**：每次掃描會為「所有新入庫嘅工」攞埋完整 JD（唔使 LLM，順便抽埋 OfferToday 刊登日期），再為最多 20 份最舊未補嘅工補 JD（`DETAIL_BACKFILL_PER_SCAN`）——個職位台會逐步全部有職位介紹，唔再淨係頭 30 份先有
- 🎯 **Match Score 過濾**：LLM 評分 0–100 + 理由；**低匹配工照樣全部顯示**（filter bar 可以一撳「低匹配」淨睇低分，或唔勾「顯示低匹配」隱藏）；**每次掃描設 LLM 預算**（預設 30 份，慳 API 錢），未處理嘅用「補齊」按鈕處理
- 📅 **新工時效過濾**：掃描時自動略過刊登日期超過期限嘅職位——**gov.hk 大灣區**（`govhk_gbayes`）限 **60 日（兩個月）**，**其餘全部（gov.hk 資訊及科技界／一般、JobsDB、OfferToday）限 14 日（兩星期，淨係收附近嘅新工）**；冇刊登日期嘅照保留。**gov.hk 列表係「由近至遠」排**，一掃到過期工就即刻停成個渠道。設定：`.env` `GBAY_MAX_JOB_AGE_DAYS`、`MAX_JOB_AGE_DAYS`（0 = 唔過濾）
- 🔢 **渠道上限（每個 track 獨立）**：**gov.hk 資訊及科技界每次頭 50 份**（`GOVHK_IT_MAX_JOBS`）、**gov.hk 一般每次頭 20 份**（`GOVHK_GENERAL_MAX_JOBS`）、**OfferToday IT 每個分類每次頭 40 份**（`OFFERTODAY_MAX_PER_SEARCH`）、**OfferToday 一般每個關鍵字搜尋每次頭 15 份**（`OFFERTODAY_GENERAL_MAX_PER_SEARCH`，每次最多 8 個搜尋字詞）。全部渠道一齊入庫，入庫時同平台同 job id 已存在就自動 skip（去重）。可選總上限 `MAX_SCAN_JOBS`（每個 track 各自適用；0 = 冇）
- ⏸ **暫停掣**：scan 進行緊時撳側邊欄「⏸ 暫停」即刻中斷（track／平台／頁之間都檢查），**已掃到嘅內容照樣入庫**，未掃嘅唔會再掃，LLM 步驟（match/CL）亦會跳過，下次 scan 先補
- 🖥️ **自動投遞（預設開）**：撳「申請」會自己填 CL + 上傳/揀 CV 並**直接撳提交**；gov.hk 用 macOS Mail **自動發送 email**（用你自己 Mail account，唔使 SMTP）。完成會自動記錄「已投遞」。Email 內文／主旨／CV 都會跟 JD 語言（中文 JD → 中文 email，英文 JD → 英文 email），而且發送前可揀 **Email 模板**（標準／簡潔／正式／直接）預覽後先發。
  - **手動模式都預填好 CV**：切去「手動（預填後等你撳）」，agent 一樣會填 CL **加上傳/揀好 CV**，淨係唔撳提交——你喺視窗 review 完撳最後一撳就會成功交到。
  - **OfferToday 用「發履歷」**：OfferToday 唔係上傳 file，而係撳「發履歷」→ 彈「選擇履歷」→ 按 JD 語言（英文/中文）自動揀你已上傳嘅對應 CV → 撳「發送」。CV 檔名關鍵字可喺設定頁或 `.env` 用 `OFFERTODAY_CV_EN_KEYWORD` / `OFFERTODAY_CV_ZH_KEYWORD` 覆寫。
  - **OfferToday 發完 CV 自動補自我介紹**：發送 CV 之後自動打一段約 100 字自我介紹再送出。IT／程式相關工用「IT 版」，其他工用「一般版」，語言跟 JD。四段文字（IT 中/英、一般中/英）都喺設定頁改或撳「AI 生成」；留空會由 AI 即場生成，LLM 唔得先 fallback 模板。IT／程式判斷關鍵字（預設已含 AI）可喺設定頁改。
  - **一齊投遞**：Dashboard 剔選多份工 →「▶ 一齊自動投遞」，一次過逐份自動提交（有進度同逐份結果）；冇 CL 嘅會自動先生成。
  - **AI 職位摘要**：每份有 JD 嘅工自動生成一句睇得明嘅摘要（做咩／核心要求／薪酬／點申請），卡片直接顯示，唔使開詳情。
  - **自我簡介（AI 可生成）**：設定頁儲低中文/英文簡介，自動嵌入每份申請嘅訊息／email 內文（喺 CL 前面）；撳「✦ AI 生成簡介」由 CV 自動寫，改好先儲存。
  - **安全閘**：驗證碼／登入牆／外部網站／缺 CL／缺 CV 一律唔會亂投，改為提示你手動完成；提交後會檢查確認頁，唔肯定就提醒你核實。
  - 想逐份工手動：詳情頁「申請動作」可以切換「自動 / 手動（預填後等你撳）」；設定頁有總開關（`AUTO_SUBMIT` / 設定頁 checkbox）。
- 🔐 **帳戶安全**：唔需要你 JobsDB/OfferToday 密碼——自動投遞用**專用 Chrome 視窗**（CDP 連接，Chrome 136+ 官方封咗預設 profile 遠端操控，所以用獨立 profile；唔會掂你原本 Chrome）。設定頁撳「🔗 開啟專用 Chrome」，**第一次喺嗰個視窗登入一次**，之後一直記住。連唔到時用後備方案（獨立 Playwright 瀏覽器，登入一次，session 存本機 `data/profiles/`）。
- 📧 **外部申請網站**（Greenhouse/Workday/公司官網）：記錄 + 俾 link，唔自動化（避免亂填公司系統）
- 📊 **Dashboard**：狀態看板（含跨平台「可能重複」標記同每份工嘅**入庫日期**）、**分頁列表**（每頁 20/50/100）+ **Filter**（狀態／平台／IT／一般／搜尋／排序：更新、入庫、刊登、匹配度／入庫日期範圍）、**本週投遞進度條（X/15）**、投遞歷史檔案庫（JD 快照 + CL 存檔）、統計圖表、面試進度追蹤、**掃描實時進度**
- ⚙️ **設定頁**：直接填 LLM key（覆蓋 .env）+ 一鍵測試連線、**由 CV 自動抽取技能清單**、CV 路徑、GBA 資格、**自動投遞開關**、**掃描設定**（IT／一般 track 開關、兩個 track 嘅關鍵字、每個來源每次掃描上限、OfferToday 一般搜尋字詞）＋**側邊欄掃描前可直接改關鍵字**（撳掃描自動儲存）

## 快速開始

```bash
./run.sh
```

1. 首次會裝 Python venv、Playwright Chromium、前端依賴。
2. `run.sh` 會檢查 `.env` 未填嘅嘢（LLM key、CV 路徑）並警告；起機後自動開瀏覽器。
3. `.env` 填好 `LLM_API_KEY`（DeepSeek）、`CV_EN_PATH` / `CV_ZH_PATH`；或者直接喺設定頁填（唔使改 .env 重啟）。
4. 設定頁填姓名/email，撳「✦ 由 CV 自動抽取」填技能清單，再撳「測試連線」確認 LLM。
5. 撳「立即掃描」——第一次會開個瀏覽器視窗，喺入面登入 JobsDB / OfferToday 一次（之後 session 會記住）。
6. 掃完喺職位台揀工 → 睇 JD + CL → 撳「開始申請」/「✉ Email 申請」（先預覽）→ 喺開咗嘅視窗/郵件 review 後提交 → 返嚟撳「✔ 記錄已投遞」。

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
  main.py            FastAPI entry + APScheduler（每 SCAN_DAY_INTERVAL 日自動掃）
  config.py          .env 設定（pydantic-settings）
  models.py          SQLite：profiles / job_applications（含 category: it|general）/ cover_letters
  routers/           profile · jobs · scan · stats
  services/
    classify.py          IT vs 一般 關鍵字分類（TrackConfig，兩個 track 共用）
    scanner.py           scan pipeline（逐 track 刮 → 去重 → 詳情 → match → CL）
    scraper_govhk.py     jobs.gov.hk（大灣區計劃 + 資訊及科技界 + 一般職位 quickview，抽 email/聯絡人）
    scraper_jobsdb.py    JobsDB（SEEK data-automation selectors）
    scraper_offertoday.py OfferToday（分類頁 + 關鍵字搜尋 + JSON-LD datePosted 抽刊登日期）
    matcher.py           keyword pre-score + LLM match score（fallback）
    cl_generator.py      CL 生成（CV 事實硬限制）
    apply_bot.py         平台內半自動預填（唔提交）
    email_bot.py         gov.hk email 申請（AppleScript 開 Mail + fallback）
frontend/            React + Vite + TS（build 入 backend/static；職位台分 IT / 一般兩頁）
data/                sqlite.db + Playwright profiles（gitignored）
```

## 資料

- `job_applications` 用 `(platform, job_id_on_platform)` 唯一約束，保證唔重複投同一份工；`category` 記住 IT / 一般，職位台分頁用
- `jd_text` 存全快照（職位下架都有得睇返）
- `cover_letters` 每次編輯 = 新 version

## Phase 2（未做）

GraphRAG 技能網絡、Text2SQL 查詢、三層記憶＋艾賓浩斯衰減、Qwen3-8B 微調、勞工處 account 網上應徵自動化、雲端部署、通知。
