"""Parser tests for jobs.gov.hk using captured live HTML fixtures."""
from pathlib import Path

from app.services.scraper_govhk import (
    extract_email_and_person,
    parse_detail_html,
    parse_joblist_html,
    parse_list_html,
    title_matches_keywords,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_list_html():
    html = (FIXTURES / "govhk_page1.html").read_text(encoding="utf-8")
    items = parse_list_html(html)
    assert len(items) == 20
    first = items[0]
    assert first["job_id"] == "21-26-0008159"
    assert first["title"] == "資訊科技工程師"
    assert "18,000" in first["salary_range"]
    assert first["location"] == "深圳"
    assert first["detail_url"].startswith("https://www2.jobs.gov.hk/0/tc/jobseeker/jobCard/?order=")


def test_parse_joblist_html():
    """資訊及科技界 search-result table parser (live joblist fixture)."""
    html = (FIXTURES / "govhk_joblist_it.html").read_text(encoding="utf-8")
    items = parse_joblist_html(html)
    assert len(items) == 20
    first = items[0]
    assert first["job_id"] == "31-26-0005281"
    assert first["title"] == "資訊科技支援技術員(EA)"
    assert first["location"] == "上水"
    assert first["salary_range"].startswith("$15,000")
    assert first["detail_url"].startswith("https://www2.jobs.gov.hk/0/tc/jobseeker/jobCard/?order=")
    assert "from=joblist" in first["detail_url"]


def test_title_keyword_filter():
    assert title_matches_keywords("資訊科技工程師")
    assert title_matches_keywords("AI基礎架構主任/高級主任")
    assert not title_matches_keywords("行政秘書")


def test_parse_detail_html():
    html = (FIXTURES / "govhk_jobcard.html").read_text(encoding="utf-8")
    d = parse_detail_html(html, "https://www2.jobs.gov.hk/0/tc/jobseeker/jobCard/?order=x")
    assert d["job_id"] == "11-26-0010247"
    assert d["title"] == "AI基礎架構主任/高級主任"
    assert d["company"] == "富融銀行有限公司"
    assert d["location"] == "深圳"
    assert d["posted_at"] == "11/08/2026"
    assert "負責多模態大模型" in d["jd_text"]
    assert d["contact_email"] == "recruitment@fusionbank.com"
    assert d["contact_person"] == "李小姐"


def test_extract_email_and_person_variants():
    note = "求職者可電郵(recruitment@fusionbank.com)履歷表給富融銀行有限公司。如要索取收集個人資料聲明, 請與李小姐(Email)聯絡。"
    email, person = extract_email_and_person(note)
    assert email == "recruitment@fusionbank.com"
    assert person == "李小姐"

    email, person = extract_email_and_person("請將履歷電郵至 a.b-c@x.y.com 俾人事部，或與王先生聯絡。")
    assert email == "a.b-c@x.y.com"
    assert person == "王先生"

    email, person = extract_email_and_person("無任何聯絡方式。")
    assert email == ""
    assert person == ""


def test_salary_range_not_truncated():
    """Regression: '$18,000 - $20,000' must not split at the comma."""
    html = (FIXTURES / "govhk_jobcard.html").read_text(encoding="utf-8")
    d = parse_detail_html(html, "https://x")
    # salary must come out whole, not truncated at the thousands comma
    assert d["salary_range"] == "每月$18,000"
