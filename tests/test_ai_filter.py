"""批量 AI 檢查（LLM 分批搵出唔係 IT 嘅工）+ 保險／地產封鎖清單。"""
import asyncio

import pytest

from app.models import JobApplication, Profile
from app.services import ai_filter
from app.services.classify import blocked_reason, tech_role_score


def _seed(db, jid, title, *, status="pending_review", category="it", jd="JD 內容",
          score=50, **kw):
    row = JobApplication(platform="offertoday", job_id_on_platform=jid, title=title,
                         company="測試公司", category=category, status=status,
                         jd_text=jd, jd_language="zh", match_score=score, **kw)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------- 保險／地產封鎖

def test_sales_agent_titles_are_blocked():
    for title in ["保險 Agent 轉行", "網路銷售代理", "Property Agent",
                  "地產經紀", "《Welcome保險agent》", "Real Estate Consultant"]:
        assert blocked_reason(title), title


def test_insurance_job_at_insurance_company_is_kept():
    """反面案例：AI Engineer @ 保險公司係真 IT 工，唔可以因為「保險」兩個字殺。"""
    assert blocked_reason("AI Engineer/AI Engineering Lead (大型保險公司)") == ""
    assert blocked_reason("AI 原生前線部署工程師") == ""      # 前線部署（FDE）
    assert blocked_reason("Software Engineer") == ""
    assert blocked_reason("網路工程師") == ""


def test_non_tech_property_roles_are_blocked():
    assert blocked_reason("物業工程師Facility Engineer")
    assert blocked_reason("高級技術員(物業維修) (EA)")


def test_tech_role_score():
    assert tech_role_score("AI Engineer") >= 1          # 有 "ai" 訊號
    assert tech_role_score("Backend Developer") >= 1
    assert tech_role_score("文員") == 0
    assert tech_role_score("") == 0


def test_custom_blocklist_replaces_builtin(db):
    """設定頁有填 = 你自訂清單（硬封鎖，完全取代內建）。"""
    from app.services.classify import resolve_blocked_keywords

    assert resolve_blocked_keywords("").__len__() > 5           # 內建
    assert resolve_blocked_keywords("車行, 補習") == ["車行", "補習"]


# --------------------------------------------------------- 批量 AI 檢查

def _stub_llm(monkeypatch, mapping, calls=None):
    """mapping: {id: (verdict, reason)}；calls 記低每次 batch 嘅 id。"""
    async def fake_chat_json(messages, temperature=0.0):
        user = messages[1]["content"]
        ids = [int(x.split("=")[1].split("｜")[0])
               for x in user.splitlines() if x.startswith("id=")]
        if calls is not None:
            calls.append(ids)
        return {"results": [{"id": i, "verdict": mapping[i][0], "reason": mapping[i][1]}
                            for i in ids if i in mapping]}

    monkeypatch.setattr(ai_filter.llm_svc, "chat_json", fake_chat_json)


def test_run_ai_check_marks_non_it_and_restores_ai(db, monkeypatch):
    keep = _seed(db, "k1", "Web Developer", score=70)
    noise = _seed(db, "n1", "物業主任 (無需經驗)", score=45)
    ai_job = _seed(db, "a1", "Forward Deployed Engineer", score=60,
                   status="low_match", match_reason=ai_filter.NON_IT_REASON_PREFIX)

    calls: list = []
    _stub_llm(monkeypatch, {
        keep.id: ("it", "真係開發工"),
        noise.id: ("non_it", "物業管理，唔係 IT"),
        ai_job.id: ("it_ai", "其實係 AI／agent 工"),
    }, calls)

    res = asyncio.run(ai_filter.run_ai_check(db, limit=10, batch_size=10))

    assert res.checked == 3 and res.batches == 1
    assert res.non_it == 1 and res.it_ai == 1 and res.it == 1

    db.expire_all()
    assert db.get(JobApplication, noise.id).status == "low_match"
    assert db.get(JobApplication, noise.id).ai_verdict == "non_it"
    assert "物業管理" in db.get(JobApplication, noise.id).match_reason
    # 之前 AI 檢查誤判 -> 還原返 pending_review
    assert db.get(JobApplication, ai_job.id).status == "pending_review"
    assert db.get(JobApplication, ai_job.id).ai_match is True
    assert db.get(JobApplication, keep.id).status == "pending_review"
    assert db.get(JobApplication, keep.id).ai_verdict == "it"


def test_run_ai_check_batches_multiple_calls(db, monkeypatch):
    rows = [_seed(db, f"b{i}", f"Software Engineer {i}") for i in range(5)]
    calls: list = []
    _stub_llm(monkeypatch, {r.id: ("it", "ok") for r in rows}, calls)

    res = asyncio.run(ai_filter.run_ai_check(db, limit=10, batch_size=2))
    assert res.checked == 5
    assert res.batches == 3                     # 2 + 2 + 1
    assert [len(c) for c in calls] == [2, 2, 1]


def test_run_ai_check_respects_limit_and_skips_done(db, monkeypatch):
    done = _seed(db, "d1", "Software Engineer", ai_verdict="it")
    applied = _seed(db, "d2", "Software Engineer", status="applied")
    rows = [_seed(db, f"c{i}", f"Software Engineer {i}") for i in range(4)]
    _stub_llm(monkeypatch, {r.id: ("it", "ok") for r in rows} | {done.id: ("it", "x")})

    res = asyncio.run(ai_filter.run_ai_check(db, limit=2, batch_size=10))
    assert res.checked == 2                     # limit 生效
    db.expire_all()
    assert db.get(JobApplication, applied.id).ai_verdict == ""   # 已投遞唔檢查
    assert db.get(JobApplication, done.id).ai_verdict == "it"    # 已檢查唔再檢查


def test_run_ai_check_survives_llm_failure(db, monkeypatch):
    _seed(db, "e1", "Software Engineer")
    from app.services.llm import LLMError

    async def boom(messages, temperature=0.0):
        raise LLMError("no key")

    monkeypatch.setattr(ai_filter.llm_svc, "chat_json", boom)
    res = asyncio.run(ai_filter.run_ai_check(db, limit=10, batch_size=10))
    assert res.checked == 0
    assert res.failed_batches == 1
    assert res.errors


def test_run_ai_check_general_track_untouched(db, monkeypatch):
    """一般工唔會洗錢做 AI 檢查（只有 IT 軌）。"""
    it_row = _seed(db, "f1", "Software Engineer")
    gen = _seed(db, "f2", "文員", category="general")
    _stub_llm(monkeypatch, {it_row.id: ("it", "ok"), gen.id: ("non_it", "no")})

    asyncio.run(ai_filter.run_ai_check(db, limit=10, batch_size=10))
    db.expire_all()
    assert db.get(JobApplication, gen.id).ai_verdict == ""


def test_missing_ids_are_left_for_next_run(db, monkeypatch):
    """LLM 漏咗某啲 id -> 唔好亂判，留返下次。"""
    a = _seed(db, "g1", "Software Engineer")
    b = _seed(db, "g2", "Software Engineer")
    _stub_llm(monkeypatch, {a.id: ("it", "ok")})       # b 冇回

    res = asyncio.run(ai_filter.run_ai_check(db, limit=10, batch_size=10))
    assert res.checked == 1
    db.expire_all()
    assert db.get(JobApplication, b.id).ai_verdict == ""


def test_prompt_forbids_dropping_ids_and_asks_it_ai():
    prompt = ai_filter._system_prompt()
    assert "it_ai" in prompt and "non_it" in prompt
    assert "每一個" in prompt                     # 唔可以漏 id
    user = ai_filter._user_prompt([{"id": 7, "title": "AI Engineer",
                                    "company": "X", "jd_text": "x" * 500}])
    assert "id=7" in user
    assert len(user) < 700           # JD 只取頭段，控制 prompt 長度


# --------------------------------------------------------- API

def test_ai_check_api_flow(client, db, monkeypatch):
    rows = [_seed(db, f"h{i}", f"Software Engineer {i}") for i in range(3)]
    _stub_llm(monkeypatch, {r.id: ("it", "ok") for r in rows})

    from app.routers import jobs as jobs_router

    jobs_router._ai_check_state["running"] = False

    pending = client.get("/api/jobs/ai-check/pending").json()
    assert pending["pending"] >= 3

    r = client.post("/api/jobs/ai-check", json={"limit": 10, "batch_size": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["started"] is True
    assert body["estimated_llm_calls"] == 1

    import time
    for _ in range(100):
        if not client.get("/api/jobs/ai-check/status").json()["running"]:
            break
        time.sleep(0.05)
    status = client.get("/api/jobs/ai-check/status").json()
    assert status["running"] is False
    assert status["checked"] >= 3


def test_ai_check_reset(client, db):
    row = _seed(db, "r1", "物業主任", status="low_match",
                match_reason=ai_filter.NON_IT_REASON_PREFIX + "：唔係 IT")
    row.ai_verdict = "non_it"
    db.commit()

    r = client.post("/api/jobs/ai-check/reset", json={})
    assert r.status_code == 200
    assert r.json()["reset"] >= 1
    db.expire_all()
    fresh = db.get(JobApplication, row.id)
    assert fresh.ai_verdict == ""
    assert fresh.status == "pending_review"       # 還原返出得嚟
