"""Previews (21 Nov, SRCH-26, SRCH-31/32, MATCH-04): leads kept rather than read, and read on demand.

A job API's lead past a search's lookups is saved as a preview: its summary, a tracked link WERA
never requests, and its CV-similarity match — after the hard filters, with no model call. It isn't
the shortlist. "Read the full ad", approving it, or "Find more" with every search sent reads it in
full: one lookup for the employer's own posting, the read ladder, a normal score — or, with no
original, a rough score on its summary.
"""

import json

import httpx
import pytest
import respx
from sqlalchemy import func, select

from wera_agent.config import wera
from wera_agent.db.models import FitReportRow, JobRow, LlmCallRow
from wera_agent.db.session import transaction
from wera_agent.domain import scoring
from wera_agent.graphs import recheck, search_pipeline
from wera_agent.nodes import fit, strategist
from wera_agent.schemas.api import DecisionIn
from wera_agent.schemas.jobs import FitEvidence
from wera_agent.services.store import Store
from wera_agent.settings import get_settings
from wera_agent.sources import extract, feeds, harvest, normalise, serper
from wera_agent.sources.firecrawl_search import SearchCaps

from .test_search_pipeline import AllowAll, world  # noqa: F401 — `world` is a fixture

REAL_JUDGE = search_pipeline.intake.judge  # before `world` replaces it

LARKSPUR = "job-preview-larkspur"  # a demo preview: Graduate Software Engineer, Payments; London, via Adzuna
ORIGINAL = "https://boards.greenhouse.io/larkspurpayments/jobs/7001"
DESCRIPTION = "You'll build Python and TypeScript services behind our card-issuing APIs, with mentoring. " * 5


def lead(
    title: str = "Graduate Software Engineer", company: str = "Larkspur Payments", i: int = 1, snippet: str = ""
) -> feeds.Lead:
    return feeds.Lead(
        url=f"https://www.adzuna.co.uk/jobs/land/ad/{6000 + i}?se=x&utm_medium=api",
        title=title,
        company=company,
        location="London, UK",
        date=None,  # undated: never hidden for its age, whatever the frozen clock says
        snippet=snippet or f"{title} at {company}: Python services for our payments platform in London.",
    )


def calls() -> int:
    with transaction() as session:
        return session.scalar(select(func.count()).select_from(LlmCallRow)) or 0


def page(_url: str) -> str:
    """The employer's own posting, with its job data, as the read ladder's first free rung reads it."""
    data = {
        "@type": "JobPosting",
        "title": "Graduate Software Engineer, Payments",
        "hiringOrganization": {"name": "Larkspur Payments"},
        "jobLocation": {"address": {"addressLocality": "London", "addressCountry": "GB"}},
        "description": DESCRIPTION,
    }
    return f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head><body></body></html>'


@pytest.fixture
def evidence(demo_jobs) -> FitEvidence:
    return FitEvidence.model_validate(demo_jobs["shortlist"][0]["evidence"])


@pytest.fixture
def live(client, outbound, monkeypatch, evidence):
    """A real install with Serper, the employer's page and the scorer replaced, and every request to
    a job API's tracked link recorded so a test can say none was made."""
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    get_settings.cache_clear()
    monkeypatch.setattr(harvest, "Robots", AllowAll)
    monkeypatch.setattr(extract, "_get", page)
    scored: list[str] = []

    def score(_session, job, _cv, *, model):
        scored.append(job.id)
        return evidence.model_copy(update={"model": model})

    monkeypatch.setattr(fit, "score", score)
    with respx.mock(assert_all_called=False) as mock:
        mock.route(host__regex=r"(^|\.)(adzuna\.[a-z.]+|jobviewtrack\.com)$").mock(return_value=httpx.Response(200))
        yield mock, scored


@pytest.fixture
def real_scoring(world, monkeypatch, evidence):  # noqa: F811
    """`world` with the real `intake.judge` back — filters, save, score, fit bar — and the scoring
    model replaced, so what a search saves and counts is what the Jobs screen would show."""
    monkeypatch.setattr(search_pipeline.intake, "judge", REAL_JUDGE)
    monkeypatch.setattr(fit, "score", lambda _s, _job, _cv, *, model: evidence.model_copy(update={"model": model}))
    return world


def tracked(mock) -> bool:
    return any(normalise.is_tracked_link(str(c.request.url)) for c in mock.calls)


def serper_finds(mock, *urls: str) -> respx.Route:
    organic = [
        {"link": u, "title": "Graduate Software Engineer, Payments - Larkspur Payments", "snippet": "Larkspur Payments, London"}
        for u in urls
    ]
    return mock.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json={"organic": organic}))


class TestSavingPreviews:
    def test_overflow_leads_are_saved_as_previews_with_no_model_call(self, store):
        before = calls()
        jobs = [
            normalise.from_lead(lead(i=i, company=f"Overflow {i}"), "adzuna", "gb", store.now, match=0.9 - i / 10)
            for i in range(3)
        ]
        assert store.save_previews(jobs) == 3
        saved = store.list_previews()
        mine = [j for j in saved if j.company.startswith("Overflow ")]
        assert [j.preview_match for j in mine] == [0.9, 0.8, 0.7]
        first = mine[0]
        assert (first.status, first.via, first.description_status, first.location.country) == (
            "preview",
            "adzuna",
            "partial",
            "GB",
        )
        assert normalise.is_tracked_link(first.apply_url)  # stored for the person, never fetched
        assert store.s.get(FitReportRow, first.id) is None and calls() == before

    def test_a_scheme_an_excluded_word_or_a_level_above_is_never_a_preview(self, store):
        jobs = [
            normalise.from_lead(
                lead("Graduate Software Engineer - Visa Sponsorship Opportunity", i=1), "adzuna", "gb", store.now
            ),
            normalise.from_lead(lead("Senior Software Engineer", i=2), "adzuna", "gb", store.now),
            normalise.from_lead(lead("Staff Software Engineer", i=3), "adzuna", "gb", store.now),
            normalise.from_lead(lead("Graduate Software Engineer", company="Kept Ltd", i=4), "adzuna", "gb", store.now),
        ]
        assert store.save_previews(jobs) == 1
        assert [
            j.title
            for j in store.list_previews()
            if j.apply_url.endswith(
                ("6001?se=x&utm_medium=api", "6002?se=x&utm_medium=api", "6003?se=x&utm_medium=api", "6004?se=x&utm_medium=api")
            )
        ] == ["Graduate Software Engineer"]
        assert not store.s.scalars(select(JobRow).where(JobRow.title.like("%Visa%"))).all()

    def test_previews_are_not_the_shortlist(self, store):
        shortlist = {j.id for j in store.list_jobs("new")}
        assert LARKSPUR not in shortlist
        assert LARKSPUR not in {j.id for j in store.list_awaiting_score(limit=100)}
        assert store.nav_counts().jobs == store.count_jobs("new")
        meta = store.shortlist_meta()
        assert meta.previews_total == 4 and meta.previews[0].id == LARKSPUR  # best match first
        assert meta.preview_cutoffs.strong == scoring.PREVIEW_STRONG

    def test_the_labels_from_the_match(self):
        assert [scoring.preview_label(m) for m in (0.95, scoring.PREVIEW_STRONG, 0.75, 0.5, None)] == [
            "strong",
            "strong",
            "good",
            "possible",
            "possible",
        ]

    def test_a_search_saves_its_previews_and_says_so(self, world, monkeypatch):  # noqa: F811
        """The pipeline's tier 0: the leads `discover_feeds` kept are saved after the filters, and
        the step's line in the run's log says how many."""
        monkeypatch.setattr(search_pipeline.feeds, "skipped", lambda f, **_k: None if f == "adzuna" else "off")
        monkeypatch.setattr("wera_agent.llm.gateway.structured", lambda *a, **k: pytest.fail("a model was asked"))
        notes: list[str] = []
        monkeypatch.setattr(search_pipeline.intake, "report", lambda _ids, **kw: notes.append(kw.get("note") or ""))

        def from_apis(planned, profile, **_kw):
            found = strategist.FeedDiscovery(by_feed={"adzuna": {"searches": 1, "leads": 2}})
            found.previews = [strategist.Preview("adzuna", "gb", lead(company=f"Acme {i}", i=i), 0.8) for i in range(2)]
            return found

        monkeypatch.setattr(search_pipeline.strategist, "discover_feeds", from_apis)
        world.answers({1: []})
        out = world.run()
        assert out.leads["previews"] == 2
        assert any("2 saved as previews" in n for n in notes)
        with transaction() as session:
            acme = [j for j in Store(session).list_previews() if j.company.startswith("Acme")]
        assert len(acme) == 2 and all(j.preview_match == 0.8 for j in acme)


class TestOnePostingOneJob:
    def test_a_preview_and_its_later_original_merge_into_one_job(self, store):
        preview = normalise.from_lead(lead(company="Merge Co"), "adzuna", "gb", store.now, match=0.81)
        store.save_previews([preview])
        original = normalise.from_scrape(
            {"title": "Graduate Software Engineer", "company": "Merge Co", "locationRaw": "London"},
            url="https://boards.greenhouse.io/mergeco/jobs/99",
            site="boards.greenhouse.io",
            markdown=DESCRIPTION,
            now=store.now,
        )
        original.via = "adzuna"
        stored, new_ids = store.upsert_jobs([original], run_ids={"board_search": "run-later"})
        # 22 Nov: new to the person, and found by the search that read it, not the one that saved it.
        assert new_ids == {preview.id} and stored[0].id == preview.id
        assert stored[0].found_by.run_id == "run-later" and stored[0].seen_at is None
        job = store._job(store._job_row(preview.id))
        assert (job.status, job.description_status, job.apply_url) == (
            "new",
            "full",
            "https://boards.greenhouse.io/mergeco/jobs/99",
        )
        assert job.preview_match == 0.81 and job.via == "adzuna"
        assert store.s.scalar(select(func.count()).select_from(JobRow).where(JobRow.company == "Merge Co")) == 1


class TestReadingAPreview:
    def test_read_with_its_original(self, api, live):
        mock, scored = live
        lookup = serper_finds(mock, ORIGINAL)
        got = api.ok("POST", f"/jobs/{LARKSPUR}/read")
        assert got["original"] is True and lookup.call_count == 1 and scored == [LARKSPUR]
        job = got["job"]
        assert job["id"] == LARKSPUR and job["status"] == "new" and job["descriptionStatus"] == "full"
        assert job["applyUrl"] == ORIGINAL and job["via"] == "adzuna" and job["fit"]["roughOnly"] is False
        assert LARKSPUR in {j["id"] for j in api.ok("GET", "/jobs")["items"]}  # on the shortlist now
        assert not tracked(mock)

    def test_read_without_an_original_is_scored_rough_on_its_summary(self, api, live):
        mock, scored = live
        serper_finds(mock)  # nothing found
        got = api.ok("POST", f"/jobs/{LARKSPUR}/read")
        assert got["original"] is False and scored == [LARKSPUR]
        job = got["job"]
        assert job["descriptionStatus"] == "partial" and job["fit"]["roughOnly"] is True
        assert job["fit"]["total"] <= scoring.ROUGH_CAP
        assert normalise.is_tracked_link(job["applyUrl"]) and not tracked(mock)
        # Not re-checked either: the sweep and the one-job check leave a tracked link alone.
        assert recheck.check_one(LARKSPUR, force=True) == "skipped"
        with transaction() as session:
            assert LARKSPUR not in {j.id for j in Store(session).recheck_candidates(500)}
        assert not tracked(mock)

    def test_a_spent_scoring_limit_refuses_before_anything_is_read(self, api, live, monkeypatch):
        mock, scored = live
        lookup = serper_finds(mock, ORIGINAL)
        monkeypatch.setattr(Store, "score_allowance", lambda _self: 0)
        assert api.code("POST", f"/jobs/{LARKSPUR}/read") == "DAILY_CAP"
        assert lookup.call_count == 0 and scored == []

    def test_demo_mode_reads_the_fixtures_full_ad_and_it_counts_towards_the_days_limit(self, api):
        """DEMO-02: no request; the fixture's ad is the original. The score is one `fit.score` call,
        counted by `scored_today` like any other (MATCH-05)."""
        with transaction() as session:
            before = Store(session).scored_today()
        got = api.ok("POST", f"/jobs/{LARKSPUR}/read")
        assert got["original"] is True and got["job"]["descriptionStatus"] == "full" and got["job"]["fit"]["total"] > 0
        with transaction() as session:
            assert Store(session).scored_today() == before + 1
        assert api.code("POST", f"/jobs/{LARKSPUR}/read") == "OK"  # read already: the score comes back


class TestShortlistingReadsFirst:
    def test_approving_a_preview_reads_it_then_approves(self, store, live):
        mock, scored = live
        lookup = serper_finds(mock, ORIGINAL)
        job = store.decide(LARKSPUR, DecisionIn(action="approve"))
        assert lookup.call_count == 1 and scored == [LARKSPUR]
        assert job.status == "in_application" and job.description_status == "full" and job.apply_url == ORIGINAL
        assert not tracked(mock)

    def test_snoozing_a_preview_needs_no_read_and_it_wakes_a_preview(self, store, live):
        mock, scored = live
        lookup = serper_finds(mock, ORIGINAL)
        row = store.s.get(JobRow, LARKSPUR)
        job = store._job(row)
        job.status, job.snoozed_until = "snoozed", "2000-01-01T00:00:00.000Z"
        from wera_agent.services.store import save_job

        save_job(store.s, job)
        store.settle()
        assert store.s.get(JobRow, LARKSPUR).status == "preview"
        store.decide(LARKSPUR, DecisionIn(action="reject", reason_code="not_my_field"))
        assert store.s.get(JobRow, LARKSPUR).status == "rejected"
        assert lookup.call_count == 0 and scored == []


class TestFindMoreReadsPreviews:
    def test_with_every_search_sent_find_more_reads_the_best_previews(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(search_pipeline.feeds, "skipped", lambda *_a, **_k: "off")
        notes: list[str] = []
        monkeypatch.setattr(search_pipeline.intake, "report", lambda _ids, **kw: notes.append(kw.get("note") or ""))
        judged: list = []
        real = search_pipeline.intake.judge

        def judging(jobs, **kw):
            judged.extend(jobs)
            return real(jobs, **kw)

        monkeypatch.setattr(search_pipeline.intake, "judge", judging)
        world.answers({1: []})
        with transaction() as session:
            planned, _ = Store(session).search_queries(refresh=False)
        queue = world.queue()
        for tier in {0, *(q.tier for q in planned)}:
            queue.mark_tier_sent(tier)
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host__regex=r"(^|\.)(adzuna\.[a-z.]+|jobviewtrack\.com)$").mock(return_value=httpx.Response(200))
            search_pipeline.run({}, now=1_762_850_000_000, caps=SearchCaps(), mode="more")
            assert not tracked(mock)
        # The two in London; Dublin's and Amsterdam's wait for a search that ticks them.
        assert "2 added to your list · short of 8: read 2 previews in full (0 had their original)" in notes
        assert [j.id for j in judged][:1] == [LARKSPUR] and {j.status for j in judged} == {"new"}

    def test_previews_left_means_find_more_isnt_used_up(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(Store, "jobs_needing_score", lambda *_a, **_k: [])
        with transaction() as session:
            planned, _ = Store(session).search_queries(refresh=False)
        queue = world.queue()
        for tier in {0, *(q.tier for q in planned)}:
            queue.mark_tier_sent(tier)
        with transaction() as session:
            assert Store(session).start_score_more().run_ids


def test_a_tracked_link_is_told_apart():
    assert normalise.is_tracked_link("https://www.adzuna.co.uk/jobs/land/ad/1?se=x")
    assert normalise.is_tracked_link("https://www.adzuna.de/jobs/land/ad/1")
    assert normalise.is_tracked_link("https://jobviewtrack.com/en-gb/job-1")
    assert not normalise.is_tracked_link("https://boards.greenhouse.io/x/jobs/1")
    assert not normalise.is_tracked_link(None)


BOARD = "https://www.totaljobs.com/jobs/graduate-software-engineer"


def hunted(i: int, title: str = "Graduate Software Engineer"):
    from wera_agent.sources.targets import Target

    return Target(
        f"https://www.totaljobs.com/job/graduate-software-engineer/{12000000 + i}",
        "totaljobs.com",
        "open",
        "q",
        i,
        title,
        "",
        None,
        country="gb",
        listing=BOARD,
    )


def hunted_previews() -> list:
    with transaction() as session:
        return [j for j in Store(session).list_previews() if j.found_on is not None and j.found_on.kind == "posting"]


class TestHuntedLinksArePreviews:
    """22 Nov: the posting links the hunter found that a search didn't read become previews at its
    end — keyed by their own address, with only the employer they name, after the hard filters, and
    with no model call — and leave the queue, so a link is one or the other."""

    def test_the_links_left_in_the_queue_become_previews_keyed_by_their_address(self, client, monkeypatch):
        import fakeredis

        from wera_agent.sources.targets import Target, TargetQueue

        monkeypatch.setattr("wera_agent.llm.gateway.structured", lambda *a, **k: pytest.fail("a model was asked"))
        before = calls()
        queue = TargetQueue(fakeredis.FakeRedis(decode_responses=True), "plan", reader="me")
        ats = Target("https://job-boards.greenhouse.io/acmerobotics/jobs/4012345678", "job-boards.greenhouse.io", "open", "q", 3,
                     "Graduate Software Engineer", "", None, country="gb", listing=BOARD)  # fmt: skip
        searched = Target("https://builtinlondon.uk/job/role-1/1000001", "builtinlondon.uk", "open", "q", 4, "Graduate Software Engineer", "", None)  # fmt: skip
        queue.push([hunted(1), hunted(2), hunted(3, "Senior Graduate Software Engineer"), ats, searched])
        saved = search_pipeline._hunted_to_previews(queue, lambda _t: True, rank=lambda items: [0.9] * len(items), boards=[], run_ids={}, now=1_762_850_000_000)  # fmt: skip
        assert saved == 3 and calls() == before
        found = {j.apply_url: j for j in hunted_previews()}
        one, two = found[hunted(1).url], found[hunted(2).url]
        # Two links with one title on one board are two previews, keyed by their addresses.
        assert one.id != two.id and one.dedupe_key == f"url:{hunted(1).url.replace('www.', '')}"
        assert (one.title, one.company, one.status, one.preview_match) == (
            "Graduate Software Engineer",
            "Totaljobs",
            "preview",
            0.9,
        )
        assert one.found_on.model_dump(by_alias=False) == {
            "site": "Totaljobs",
            "page": BOARD,
            "kind": "posting",
            "employer_known": False,
        }
        # The employer its address names, and that is known; the excluded title never saved.
        acme = found[ats.url]
        assert acme.company == "Acmerobotics" and acme.found_on.employer_known and acme.dedupe_key.startswith("ats:greenhouse:")
        assert hunted(3).url not in found
        # Out of the queue — a link is queued or a preview, never both — and what a search found stays.
        assert [t.url for t in queue.peek()] == [searched.url]

    def test_reading_one_reads_its_address_with_no_lookup(self, api, live, store):
        mock, scored = live
        lookup = serper_finds(mock, ORIGINAL)
        job = normalise.from_hunted(hunted(7).url, title="Graduate Software Engineer", employer=None, site="Totaljobs", listing=BOARD, now=store.now, match=0.85)  # fmt: skip
        store.save_previews([job])
        store.s.commit()
        got = api.ok("POST", f"/jobs/{job.id}/read")
        assert got["original"] is True and lookup.call_count == 0 and scored == [job.id]
        read = got["job"]
        assert read["id"] == job.id and read["descriptionStatus"] == "full" and read["applyUrl"] == hunted(7).url
        assert read["company"] == "Larkspur Payments"  # the posting names its employer
        assert not tracked(mock)

    def test_one_robots_txt_disallows_isnt_read_and_stays_a_preview(self, api, live, store, monkeypatch):
        mock, scored = live

        class Refuses:
            def allows(self, _url: str) -> bool:
                return False

        monkeypatch.setattr(harvest, "Robots", Refuses)
        monkeypatch.setattr(extract, "_get", lambda _u: pytest.fail("a disallowed page was fetched"))
        lookup = serper_finds(mock, ORIGINAL)
        job = normalise.from_hunted(hunted(8).url, title="Graduate Software Engineer", employer=None, site="Totaljobs", listing=BOARD, now=store.now)  # fmt: skip
        store.save_previews([job])
        store.s.commit()
        assert api.code("POST", f"/jobs/{job.id}/read") == "PREVIEW_UNREADABLE"
        assert lookup.call_count == 0 and scored == []
        with transaction() as session:
            assert session.get(JobRow, job.id).status == "preview"


class TestAShortSearchReadsItsPreviews:
    """22 Nov: a search short of 8 with nothing left to read or send reads its best previews in
    full, best match first, until it has 8 or its budgets end — not only "Find more"."""

    def _hunted(self, n: int, match: float) -> list[str]:
        from .test_search_pipeline import posting

        with transaction() as session:
            store = Store(session)
            jobs = [
                normalise.from_hunted(
                    posting(400 + i),
                    title="Graduate Software Engineer",
                    employer=None,
                    site="Built In London",
                    listing="https://builtinlondon.uk/jobs",
                    now=store.now,
                    match=match - i / 1000,  # best first, in order
                )  # fmt: skip
                for i in range(n)
            ]
            store.save_previews(jobs)
        return [posting(400 + i) for i in range(n)]

    def test_short_of_eight_it_reads_the_best_previews_until_eight(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(search_pipeline.feeds, "skipped", lambda *_a, **_k: "off")
        notes: list[str] = []
        monkeypatch.setattr(search_pipeline.intake, "report", lambda _ids, **kw: notes.append(kw.get("note") or ""))
        links = self._hunted(10, 0.99)  # better than the demo's job API previews
        world.answers({1: [posting_url(0)]})
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host__regex=r"(^|\.)(adzuna\.[a-z.]+|jobviewtrack\.com)$").mock(return_value=httpx.Response(200))
            out = world.run()
            assert not tracked(mock)
        # One from the search, then a round of eight previews: nine, and it stops there.
        assert out.previews_read == 8 and out.surfaced == 9 and out.short is None
        # Read where they are, the best eight (three at a time on Postgres, so in any order).
        assert sorted(u for u in world.read if u in links) == sorted(links[:8])
        assert "8 added to your list · short of 8: read 8 previews in full (8 had their original)" in notes
        assert not set(links[8:]) & set(world.read)  # the other two wait

    def test_a_full_search_reads_none(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(search_pipeline.feeds, "skipped", lambda *_a, **_k: "off")
        links = self._hunted(3, 0.99)
        world.answers({1: [posting_url(i) for i in range(12)]})
        out = world.run()
        assert out.previews_read == 0 and out.surfaced == 12
        assert not set(links) & set(world.read)

    def test_out_of_lookups_a_good_preview_is_scored_on_its_summary_and_a_possible_one_waits(
        self,
        world,  # noqa: F811
        monkeypatch,
        real_scoring,
    ):
        """22 Nov (SRCH-26, MATCH-04): the search's one allowance spent, a job API's preview at Good
        or better is scored rough-only on its summary, capped, through the fit bar and the day's
        limit, rather than waiting for the person; one under Good waits. A hunted link is still read
        where it is, and no tracked link is requested."""
        monkeypatch.setattr(search_pipeline.feeds, "skipped", lambda *_a, **_k: "off")
        monkeypatch.setitem(wera().__dict__, "lookups_per_search", 0)
        notes: list[str] = []
        monkeypatch.setattr(search_pipeline.intake, "report", lambda _ids, **kw: notes.append(kw.get("note") or ""))
        links = self._hunted(1, 0.1)  # the worst match of all, and still read
        with transaction() as session:
            store = Store(session)
            possible = normalise.from_lead(lead(company="Possible Co", i=9), "adzuna", "gb", store.now, match=0.6)
            store.save_previews([possible])
        world.answers({1: []})
        with respx.mock(assert_all_called=False) as mock:
            mock.route(host__regex=r"(^|\.)(adzuna\.[a-z.]+|jobviewtrack\.com)$").mock(return_value=httpx.Response(200))
            out = world.run()
            assert not tracked(mock)
        assert not any(normalise.is_tracked_link(u) for u in world.read)
        assert set(links) <= set(world.read)
        # The demo's previews: Larkspur 0.86 (Strong) and Fenwick 0.77 (Good), on their summary;
        # a London lead at 0.6 (Possible) waits, and so do Dublin's and Amsterdam's, places not ticked.
        good = {LARKSPUR, "job-preview-fenwick"}
        with transaction() as session:
            store = Store(session)
            for job_id in good:
                job = store.get_job(job_id)
                assert job.status == "new" and job.description_status == "partial", job_id
                assert job.fit.rough_only and job.fit.total <= scoring.ROUGH_CAP, job_id
            for job_id in (possible.id, "job-preview-saltire", "job-preview-brackenbury"):
                assert session.get(JobRow, job_id).status == "preview", job_id
        assert out.previews_read == 3 and good <= out.new_ids
        assert any("short of 8: read 1 preview in full (1 had their original), 2 scored on their summary (no lookups left)" in n for n in notes), notes  # fmt: skip


class TestOneLookupAllowance:
    """22 Nov: the job APIs' snippet leads, aggregators' copies and a short search's previews draw
    on one allowance a search — one client, `lookups_per_search` — and the APIs, which go first,
    may take two-thirds of it at most, for good matches only."""

    def test_the_apis_the_hunter_and_the_previews_share_one_client(self, world, monkeypatch):  # noqa: F811
        from wera_agent.sources import hunt, previews

        seen: dict[str, object] = {}
        monkeypatch.setattr(search_pipeline.feeds, "skipped", lambda f, **_k: None if f == "adzuna" else "off")

        def from_apis(planned, profile, *, resource, lookups, bar, **_kw):
            seen.update(apis=resource, share=lookups, bar=bar)
            return strategist.FeedDiscovery()

        monkeypatch.setattr(search_pipeline.strategist, "discover_feeds", from_apis)
        real_hunter = hunt.Hunter

        def hunter(**kw):
            made = real_hunter(**kw)
            seen["hunter"] = made
            return made

        monkeypatch.setattr(search_pipeline.hunt, "Hunter", hunter)
        real_read = previews.read_many

        def read_many(found, *, serper, **kw):
            seen["previews"] = serper
            return real_read(found, serper=serper, **kw)

        monkeypatch.setattr(previews, "read_many", read_many)
        world.answers({1: []})
        world.run()
        total = wera().lookups_per_search
        assert seen["share"] == strategist.api_lookups(total) == 10 and total == 15 and seen["bar"] == scoring.PREVIEW_GOOD
        assert seen["apis"] is seen["previews"] is seen["hunter"]._make_lookup()  # type: ignore[attr-defined]
        assert seen["apis"].max_queries == total  # type: ignore[attr-defined]

    def test_the_apis_look_only_for_good_matches_within_their_share(self, store, monkeypatch):
        from .test_feeds import FakeSerper

        monkeypatch.setattr(feeds, "skipped", lambda f, **_k: None if f == "adzuna" else "off")
        leads = [
            lead(f"Graduate Software Engineer {t}", company=f"{t} Ltd", i=i)
            for i, t in enumerate(("Cloud", "Data", "Ops", "Risk"))
        ]
        monkeypatch.setattr(feeds.adzuna, "search", lambda _q, _g: (leads, "api"))
        order = {"Cloud": 0.95, "Data": 0.9, "Ops": 0.85, "Risk": 0.5}
        rank = lambda items: [order[t.rsplit(" ", 1)[-1]] for t, _ in items]  # noqa: E731
        import fakeredis

        from wera_agent.sources.targets import TargetQueue

        redis = fakeredis.FakeRedis(decode_responses=True)
        serper_ = FakeSerper({})
        serper_.max_queries = 15
        planned = [p for p in store.search_queries(refresh=False)[0] if p.gl == "gb"][:1]
        found = strategist.discover_feeds(planned, store.get_profile(), queue=TargetQueue(redis, "plan"), known_urls=set(), sites=[], now=store.now, redis=redis, resource=serper_, rank=rank, lookups=2, bar=scoring.PREVIEW_GOOD)  # fmt: skip
        asked = [q.split('"')[1].rsplit(" ", 1)[-1] for q in serper_.asked]
        assert asked == ["Cloud", "Data"]  # the share, best first; Ops waits, Risk is under the bar
        assert {p.lead.title.rsplit(" ", 1)[-1] for p in found.previews} == {"Cloud", "Data", "Ops", "Risk"}
        assert not any(normalise.is_tracked_link(q) for q in serper_.asked)

    def test_the_apis_lookups_give_each_ticked_country_its_floor_first(self, store, monkeypatch):
        """SRCH-32 (22 Nov): Switzerland's leads above the bar take their floor of the APIs' share
        even when Britain's match better; the rest go best match first."""
        from .test_feeds import FakeSerper

        monkeypatch.setattr(feeds, "skipped", lambda f, **_k: None if f == "adzuna" else "off")
        names = {"gb": ("Cloud", "Data", "Ops", "Risk"), "ch": ("Zug", "Basel")}
        by_country = {
            c: [lead(f"Graduate Software Engineer {t}", company=f"{t} AG", i=10 * k + i) for i, t in enumerate(ts)]
            for k, (c, ts) in enumerate(names.items())
        }
        monkeypatch.setattr(feeds.adzuna, "search", lambda q, _g: (by_country.get(q.country, []), "api"))
        order = {"Cloud": 0.95, "Data": 0.9, "Ops": 0.88, "Risk": 0.86, "Zug": 0.75, "Basel": 0.6}
        rank = lambda items: [order[t.rsplit(" ", 1)[-1]] for t, _ in items]  # noqa: E731
        import fakeredis

        from wera_agent.sources.targets import TargetQueue

        redis = fakeredis.FakeRedis(decode_responses=True)
        serper_ = FakeSerper({})
        serper_.max_queries = 15
        planned = store.search_queries(refresh=False)[0]
        planned = [next(p for p in planned if p.gl == c) for c in ("gb", "ch")]
        strategist.discover_feeds(planned, store.get_profile(), queue=TargetQueue(redis, "plan"), known_urls=set(), sites=[], now=store.now, redis=redis, resource=serper_, rank=rank, lookups=4, bar=scoring.PREVIEW_GOOD)  # fmt: skip
        asked = {q.split('"')[1].rsplit(" ", 1)[-1] for q in serper_.asked}
        # A floor of 2 each: Zug, then Basel under the bar gives its slot back to Britain's best.
        assert asked == {"Cloud", "Data", "Ops", "Zug"}, asked
        assert len(serper_.asked) <= 4


def posting_url(i: int) -> str:
    from .test_search_pipeline import posting

    return posting(i)


class TestAReadPreviewIsFoundByItsSearch:
    """22 Nov (SRCH-26): a preview a search reads into a job is found by that search — its run on
    `foundBy`, unseen, counted in the run's new jobs — and the log says what reached the list. On
    22 Nov a search read 8 previews onto the list and said "0 scored"; its run said 1 new job, and
    the 8 kept the earlier run that had saved them, so "Just found" didn't show them."""

    def test_a_short_search_counts_the_previews_it_read_and_one_under_the_bar(
        self,
        world,  # noqa: F811
        real_scoring,
        monkeypatch,
    ):
        monkeypatch.setattr(search_pipeline.feeds, "skipped", lambda *_a, **_k: "off")
        notes: list[str] = []
        monkeypatch.setattr(search_pipeline.intake, "report", lambda _ids, **kw: notes.append(kw.get("note") or ""))
        with transaction() as session:
            store = Store(session)
            saved = [
                normalise.from_lead(lead(company=f"Read Co {i}", i=20 + i), "adzuna", "gb", store.now, match=0.9 - i / 100)
                for i in range(3)
            ]
            store.save_previews(saved, run_ids={"board_search": "run-earlier"})
            store.mark_seen(saved[0].id)  # opened as a preview: still new once a search reads it
        low = saved[2].id
        real_bar = Store.fit_bar

        def bar(self, totals, *, apply=True):
            return real_bar(self, [(i, 0.0 if i == low else t) for i, t in totals], apply=apply)

        monkeypatch.setattr(Store, "fit_bar", bar)
        world.answers({1: []})
        out = search_pipeline.run({"board_search": "run-now"}, now=1_762_850_000_000, caps=SearchCaps())
        listed = {saved[0].id, saved[1].id, LARKSPUR, "job-preview-fenwick"}
        assert out.new_ids == listed and low not in out.new_ids
        with transaction() as session:
            store = Store(session)
            for job_id in listed:
                job = store.get_job(job_id)
                assert job.status == "new" and job.found_by.run_id == "run-now" and job.seen_at is None, job_id
            assert session.get(JobRow, low).status == "hidden"
        line = next(n for n in notes if "short of 8" in n)
        assert line == "4 added to your list, 1 under your bar · short of 8: read 5 previews in full (0 had their original)"
        assert any(n.startswith("4 added to your list, 1 under your bar · read 5 postings") for n in notes), notes

    def test_reading_the_full_ad_makes_it_new_again(self, api, live, store):
        """The person's own "Read the full ad" has no run of its own: the job is unseen, so it is
        "New" and sorts under "Just found" — dated now when the preview had no run on it (the
        demo's), else keeping the run that saved it."""
        mock, _scored = live
        serper_finds(mock, ORIGINAL)
        store.mark_seen(LARKSPUR)  # opened as a preview
        store.s.commit()
        job = api.ok("POST", f"/jobs/{LARKSPUR}/read")["job"]
        assert job.get("seenAt") is None and job["foundBy"]["channel"] == "board_search" and job["foundBy"]["at"]
        listed = {j["id"]: j for j in api.ok("GET", "/jobs")["items"]}
        assert LARKSPUR in listed and listed[LARKSPUR].get("seenAt") is None

    def test_read_with_no_run_it_keeps_the_run_that_saved_it(self, store):
        preview = normalise.from_lead(lead(company="Kept Co", i=40), "adzuna", "gb", store.now, match=0.8)
        store.save_previews([preview], run_ids={"board_search": "run-earlier"})
        store.mark_seen(preview.id)
        stored, new_ids = store.upsert_jobs([previews_unread(store, preview.id)])
        assert new_ids == {preview.id} and stored[0].found_by.run_id == "run-earlier" and stored[0].seen_at is None


def previews_unread(store, job_id: str):
    from wera_agent.sources import previews

    return previews.unread(store._job(store._job_row(job_id)))
