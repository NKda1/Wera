class TestNotAJobButAScheme:
    """20 Nov, live: a trading "academy" selling a Skilled Worker visa route scored 4.2 and had a
    pack drafted for it. Adverts for a paid scheme or a visa are not jobs."""

    def _job(self, title: str, description: str):
        from wera_agent.schemas.jobs import Job, JobSource, Location

        return Job.model_validate(
            {
                "id": "job-s", "dedupe_key": "url:https://s.example/1", "title": title, "company": "Educate2trade Ltd",
                "location": Location(city="London", raw="London"), "remote": None, "description_md": description,
                "description_status": "full", "apply_url": "https://s.example/1",
                "sources": [JobSource(channel="board_search", detail="s.example", seen_at="2026-11-20T09:00:00.000Z")],
                "language": "en", "first_seen": "2026-11-20T09:00:00.000Z", "last_seen": "2026-11-20T09:00:00.000Z",
                "status": "new", "hidden_reason": None,
            }
        )  # fmt: skip

    def test_a_title_selling_a_visa_is_not_a_job(self):
        from wera_agent.domain import filters

        job = self._job("Junior Financial Analyst / Trader UK Work Visa Opportunity", "x" * 300)
        assert filters.scheme_reason(job) == filters.SCHEME

    def test_an_academy_that_hands_out_capital_is_not_a_job(self):
        from wera_agent.domain import filters

        text = "Route 2: Academy Pathway for graduates. Analysts manage firm-allocated capital once trained."
        assert filters.scheme_reason(self._job("Financial Analyst", text)) == filters.SCHEME

    def test_ordinary_postings_are_left_alone(self):
        from wera_agent.domain import filters

        for text in (
            "Visa sponsorship available for the right candidate.",
            "You will manage supplier milestones and placement fees tied to start dates.",
            "Our graduate programme fees are covered in full.",
        ):
            assert filters.scheme_reason(self._job("Procurement Analyst", text)) is None
