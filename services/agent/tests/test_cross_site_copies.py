"""NORM-06 across sites: one job seen on several sites is one job, at the employer's link.

Found by the live search of 13 Nov: "Junior Software Engineer" at Abound was saved from bebee
(an aggregator's copy, queued by an open search) and again from Ashby (the employer's system,
found via Adzuna), because their links differ. The rule must merge those and never merge two
real jobs — two openings on one site, a Junior and a Senior, the 2026 and 2027 intakes, or a
title whose words are inside another's.
"""

from wera_agent.clock import iso, now_ms
from wera_agent.db.models import ApplicationRow, JobRow
from wera_agent.domain import dedupe
from wera_agent.schemas.jobs import Ats, Job, JobSource, Location
from wera_agent.services.store import save_job

ASHBY = "https://jobs.ashbyhq.com/Abound/582538c4-9b6e-464f-932e-eb4a4337bc7b"
BEBEE = "https://bebee.com/gb/jobs/junior-software-engineer-abound-london--fj-2441986498"
BUILTIN = "https://builtinlondon.uk/job/junior-software-engineer/11991410"


def job(job_id: str, url: str, title: str = "Junior Software Engineer", **over) -> Job:
    at = iso(now_ms())
    base = {
        "id": job_id,
        "dedupe_key": dedupe.dedupe_key(ats=None, apply_url=url, company="Abound", title=title, city="London"),
        "title": title,
        "company": "Abound",
        "location": Location(city="London", country="GB", raw="London"),
        "remote": None,
        "description_md": "Build lending software. " * 20,
        "description_status": "full",
        "apply_url": url,
        "sources": [JobSource(channel="board_search", detail=dedupe._host(url), seen_at=at)],
        "language": "en",
        "first_seen": at,
        "last_seen": at,
        "status": "new",
    }
    return Job.model_validate(base | over)


class TestTheRule:
    def test_the_same_job_on_another_site_is_a_copy(self):
        assert dedupe.is_copy_elsewhere(job("a", BEBEE), job("b", ASHBY))

    def test_two_openings_on_one_site_are_two_jobs(self):
        assert not dedupe.is_copy_elsewhere(
            job("a", BUILTIN), job("b", "https://builtinlondon.uk/job/junior-software-engineer/11991999")
        )

    def test_a_junior_and_a_senior_are_two_jobs(self):
        # token_set_ratio scores these as the same title; the level words say otherwise.
        assert not dedupe.is_copy_elsewhere(
            job("a", BUILTIN, "Junior Backend Software Engineer"), job("b", ASHBY, "Senior Software Engineer, Backend")
        )

    def test_two_intakes_are_two_jobs(self):
        assert not dedupe.is_copy_elsewhere(
            job("a", BUILTIN, "Graduate Engineer 2026"), job("b", ASHBY, "Graduate Engineer 2027")
        )

    def test_a_title_inside_another_isnt_the_same_title(self):
        assert not dedupe.is_copy_elsewhere(
            job("a", BUILTIN, "Graduate Machine Learning Engineer 2027"),
            job("b", ASHBY, "Graduate Software Engineer (AI ML Test Systems) 2027"),
        )

    def test_two_feed_jobs_with_different_ids_are_two_jobs(self):
        one = job("a", ASHBY, ats=Ats(vendor="ashby", board_token="Abound", job_id="1"))
        two = job(
            "b", "https://boards.greenhouse.io/abound/jobs/2", ats=Ats(vendor="greenhouse", board_token="abound", job_id="2")
        )
        assert not dedupe.is_copy_elsewhere(one, two)

    def test_a_merge_keeps_the_employers_link(self):
        merged = dedupe.merge_sources(job("a", BEBEE), job("b", ASHBY))
        assert merged.apply_url == ASHBY and len(merged.sources) == 2
        assert dedupe.merge_sources(job("c", ASHBY), job("d", BEBEE)).apply_url == ASHBY


class TestSaving:
    def test_a_copy_found_later_joins_the_saved_job(self, store):
        store.upsert_jobs([job("job-bebee", BEBEE)])
        stored, new = store.upsert_jobs([job("job-ashby", ASHBY, via="adzuna")])
        assert new == set() and [j.id for j in stored] == ["job-bebee"]
        row = store.s.get(JobRow, "job-bebee")
        assert row.data["applyUrl"] == ASHBY and row.dedupe_key == dedupe.key_for(stored[0])
        assert {s["detail"] for s in row.data["sources"]} == {"bebee.com", "jobs.ashbyhq.com"}
        assert row.data["via"] == "adzuna" and store.s.get(JobRow, "job-ashby") is None

    def test_a_second_opening_on_the_same_site_is_saved(self, store):
        store.upsert_jobs([job("job-1", BUILTIN)])
        _, new = store.upsert_jobs([job("job-2", "https://builtinlondon.uk/job/junior-software-engineer/11991999")])
        assert new == {"job-2"}


class TestTheRepair:
    def test_copies_saved_before_the_rule_are_merged_into_the_employers(self, store):
        for j in (job("job-bebee", BEBEE), job("job-builtin", BUILTIN), job("job-ashby", ASHBY)):
            save_job(store.s, j, questions=[])  # as saved before the rule: no merge on the way in
        store.s.flush()
        [found] = [f for f in store.dedupe_saved() if f["company"] == "Abound"]
        assert (found["action"], found["keep"]) == ("would merge", "job-ashby")
        assert store.s.get(JobRow, "job-bebee").status == "new"  # a dry run changes nothing

        store.dedupe_saved(apply=True)
        kept = store.s.get(JobRow, "job-ashby")
        assert len(kept.data["sources"]) == 3
        for copy in ("job-bebee", "job-builtin"):
            row = store.s.get(JobRow, copy)
            assert (row.status, row.data["hiddenReason"]) == (
                "hidden",
                "Duplicate: the same job as job-ashby, seen on another site",
            )

    def test_the_copy_with_an_application_is_the_one_kept(self, store):
        for j in (job("job-bebee", BEBEE), job("job-ashby", ASHBY)):
            save_job(store.s, j, questions=[])  # as saved before the rule: no merge on the way in
        store.s.add(ApplicationRow(id="app-x", job_id="job-bebee", status="ready_for_review", created_at=iso(now_ms())))
        store.s.flush()
        store.dedupe_saved(apply=True)
        kept = store.s.get(JobRow, "job-bebee")
        assert kept.status == "new" and kept.data["applyUrl"] == ASHBY  # it applies at the employer now
        assert store.s.get(JobRow, "job-ashby").status == "hidden"


class TestTrackingTags:
    def test_a_campaign_tag_does_not_make_a_second_job(self):
        """14 Nov: Intuit's `?cid=directBookmarked_…` saved one posting twice."""
        base = "https://jobs.intuit.com/job/london/graduate-software-engineer-1/27595/103261389616"
        assert dedupe.canonical_url(f"{base}?cid=directBookmarked_directBookmarked") == base
        assert dedupe.canonical_url(f"{base}?gclid=abc&fbclid=def&msclkid=g&trk=h") == base

    def test_a_parameter_that_only_starts_like_one_is_kept(self):
        assert dedupe.canonical_url("https://x.com/j?cidx=4&id=9") == "https://x.com/j?cidx=4&id=9"
