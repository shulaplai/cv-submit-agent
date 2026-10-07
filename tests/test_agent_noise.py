"""「agent」噪音防護（用戶投訴 m01290）。

背景：IT 軌出現 112 份非 IT 工（Hotline Agent／Property Agent／報關 agent…），
全部係因為裸 "agent" 同時出現喺三個地方：

  1. `classify.DEFAULT_IT_KEYWORDS`（標題命中就入 IT 軌）
  2. `jobflags` AI 標題字（標題命中就標 ai_match=1）
  3. `tuning.priority_keywords()`（做咗豁免字 -> 無視渠道上限照收）

修法：IT 關鍵字改用 "ai agent"／"agent developer"／"agentic"；AI 判斷將
"agent" 當**弱字**（要有技術語境先算）；豁免字唔再用弱字。
呢個檔守住呢三條防線，唔想將來有人「順手」加返裸 "agent"。
"""
from app.services import tuning
from app.services.classify import (DEFAULT_IT_KEYWORDS, TrackConfig, classify,
                                   resolve_it_keywords)
from app.services.jobflags import ai_match, ai_relevance
from app.services.tuning import matches_ai_term, priority_keywords


# ------------------------------------------------------- 1. IT 軌分類

NOISE_TITLES = [
    "Hotline Agent",
    "Part Time Reservation Agent",
    "Property Agent",
    "Real Estate Consultant / Agent",
    "Customs Clearance Support Agent",
    "HeadHunt Agent 獵頭",
    "Guest Service Agent",
    "assistant site agent",
    "Hong Kong Patent Agent",
    "Meet & Assist Agent",
]

REAL_AI_TITLES = [
    "Agent Hub Algorithm Engineer",
    "agent架构师",
    "创始工程师（agent）",
    "AI Agent 開發工程師",
    "Agent Builder Trainee",
    "AI Engineer",
    "Software Developer",
]


def test_bare_agent_not_in_default_it_keywords():
    assert "agent" not in DEFAULT_IT_KEYWORDS
    assert "ai agent" in DEFAULT_IT_KEYWORDS
    assert "agent developer" in DEFAULT_IT_KEYWORDS


def test_noise_titles_are_not_classified_as_it():
    kws = resolve_it_keywords("")
    for title in NOISE_TITLES:
        assert classify(title, kws) != "it", f"{title!r} 唔應該入 IT 軌"


def test_real_ai_titles_still_classified_as_it():
    kws = resolve_it_keywords("")
    for title in REAL_AI_TITLES:
        assert classify(title, kws) == "it", f"{title!r} 應該入 IT 軌"


# ------------------------------------------------------- 2. AI 相關

def test_agent_title_needs_tech_context():
    # 弱字 agent：冇技術語境就唔算 AI
    for title in NOISE_TITLES:
        assert ai_match(title) is False, f"{title!r} 唔應該標 AI"
    # 有技術語境（AI／工程師／架構／algorithm）就照算
    assert ai_match("Agent Hub Algorithm Engineer") is True
    assert ai_match("agent架构师") is True
    assert ai_match("AI Agent 開發工程師") is True
    assert ai_match("Agent Developer") is True


def test_strong_ai_keyword_still_hits_title():
    assert ai_relevance("AI Engineer") == "title"
    assert ai_relevance("Agentic Workflow Builder") == "title"


def test_weak_agent_can_still_come_from_jd():
    """標題只有 agent 但 JD 講明用 LLM -> 當 JD 級 AI 相關。"""
    assert ai_relevance("Customer Success Agent",
                        "使用 LLM 同 AI Agent 平台") == "jd"
    assert ai_relevance("Customer Success Agent", "接聽電話、處理查詢") == ""


# ------------------------------------------------------- 3. 豁免字

def test_priority_keywords_never_contain_bare_agent(db):
    t = tuning.load_tuning(db)
    kws = [k.lower() for k in priority_keywords(t)]
    assert "agent" not in kws, "裸 agent 做豁免字 -> 非 IT 工會無視渠道上限"
    assert "ai" in kws


def test_matches_ai_term_ignores_bare_agent():
    terms = ["AI Agent", "AI", "agentic"]
    assert matches_ai_term("AI Agent", terms) is True
    assert matches_ai_term("AI", terms) is True
    assert matches_ai_term("agent", terms) is False


# ------------------------------------------------------- 4. 搜尋字詞

def test_offertoday_it_targets_skip_bare_agent_terms():
    from app.services import scraper_offertoday as so

    cfg = TrackConfig.defaults("it")
    cfg.ai_search_terms = ["AI Agent", "AI", "agentic"]
    cfg.ai_search_max_searches = 3
    # 用戶舊設定用咗裸 agent
    cfg.offertoday_search_terms = ["agent", "developer", "AI", "FDE"]

    queries = [q for _u, q, _ai in so._search_targets(cfg) if q != "category"]
    # AI 組字詞行一次（喺 AI 組），其他字詞唔會再重複
    assert queries.count("AI") == 1
    # 弱字 agent 命中唔到 AI 組字詞 -> 留喺「其他字詞」，但 IT 標題過濾會擋走
    # 佢搵到嘅 Hotline Agent（見上面 test_noise_titles_are_not_classified_as_it）
    assert "developer" in queries and "FDE" in queries


def test_ai_group_max_age_uses_channel_terms():
    from app.services.scanner import _ai_group_max_age

    cfg = TrackConfig.defaults("it")
    cfg.ai_search_terms = ["AI Agent", "AI"]
    cfg.ai_search_max_age_days = 7
    assert _ai_group_max_age("AI", cfg, 14) == (7, True)
    assert _ai_group_max_age("AI Agent", cfg, 14) == (7, True)
    assert _ai_group_max_age("developer", cfg, 14) == (14, False)
