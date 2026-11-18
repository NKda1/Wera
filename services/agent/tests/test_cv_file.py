"""EXP-01, 18 Nov: the CV PDF is rendered by RenderCV from a CV file the person can edit.

The file is a real RenderCV document (`cv:`, `design:`, `locale:`) built from the pack, with no
contact details in it; they are added when it is rendered (CV-06, EXP-02). The PDF has to read
cleanly to a parser in every theme, and the same pack has to render the same bytes (EXP-03).
"""

import io
import json
import os
from datetime import UTC, datetime

import pypdf
import pytest

from wera_agent.export import cv_pdf, cv_yaml
from wera_agent.paths import FIXTURES_DIR
from wera_agent.schemas.cv import Contact
from wera_agent.schemas.cv_file import CvFile
from wera_agent.schemas.jobs import Job, Location
from wera_agent.schemas.pack import Cited, CvItem, CvSection, GuardResult, TailoredCV

ON = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)
CONTACT = Contact(email="jordan.ellery@example.com", phone="+44 7700 900123", address="London")


def tailored(cv, *, summary: str = "Graduate building production AI tooling.", extra_role: str | None = None) -> TailoredCV:
    role = cv.experience[0]
    items = [CvItem(role_id=role.id, bullets=[Cited(text=b.text, source_ids=[b.id]) for b in role.bullets[:2]])]
    if extra_role:
        items.append(CvItem(role_id=extra_role, bullets=[]))
    project = cv.projects[0]
    return TailoredCV(
        job_id="job-1",
        cv_version=cv.version,
        summary=Cited(text=summary, source_ids=["sum-01"]),
        sections=[
            CvSection(kind="experience", items=items),
            CvSection(
                kind="projects",
                items=[
                    CvItem(role_id=project.id, bullets=[Cited(text=project.bullets[0].text, source_ids=[project.bullets[0].id])])
                ],
            ),
        ],
        skills=["Python", "FastAPI", "C#"],
        omitted_bullet_ids=[],
        guard=GuardResult(pass_=True, violations=[], repaired=False),
    )


def extracted(pdf: bytes) -> str:
    return "\n".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)


def words(text: str) -> str:
    return " ".join(text.replace("​", "").split())


class TestTextInAndOut:
    @pytest.mark.parametrize(
        "line",
        [
            "Cut costs 40% & shipped C# and F# services",
            "Renamed fees_owed and _private_ fields",
            'Wrote #rust and #read("x") as hashtags',
            "Priced at $5, then $$x$$ by mistake",
            "Used *stars*, **bold** and [a](link) literally",
            "Compared a < b > c and <b>tags</b>",
            "!!! not an admonition",
            "Back\\slash and ~tilde @handle 1/2",
        ],
    )
    def test_a_line_prints_as_written(self, line):
        assert cv_yaml.plain(cv_yaml.escape(line)) == line

    def test_a_backtick_becomes_an_apostrophe(self):
        # A backtick opens raw Typst whether or not Markdown escapes it.
        assert cv_yaml.plain(cv_yaml.escape("ran `make`")) == "ran 'make'"

    @pytest.mark.parametrize(
        "degree,split",
        [
            ("BSc Computer Science", ("BSc", "Computer Science")),
            ("BA (Hons) in Economics", ("BA (Hons)", "Economics")),
            ("MEng Electronic Engineering", ("MEng", "Electronic Engineering")),
            ("PhD in Physics", ("PhD", "Physics")),
            ("Bachelor of Science in Biology", (None, "Bachelor of Science in Biology")),
        ],
    )
    def test_a_degree_splits_into_qualification_and_subject(self, degree, split):
        assert cv_yaml.split_degree(degree) == split


class TestBuild:
    def test_the_file_holds_no_contact_details(self, demo_cv):
        document, _ = cv_yaml.build(tailored(demo_cv), demo_cv)
        text = cv_yaml.dump(document)
        for detail in (CONTACT.email, CONTACT.phone, "7700"):
            assert detail not in text
        assert not {"email", "phone", "photo"} & set(document["cv"])

    def test_role_headers_and_dates_come_from_the_master_cv(self, demo_cv):
        """TAIL-02: the pack carries bullets; titles, organisations and dates are the master CV's."""
        document, _ = cv_yaml.build(tailored(demo_cv), demo_cv)
        role = demo_cv.experience[0]
        entry = document["cv"]["sections"]["Experience"][0]
        assert (entry["position"], entry["company"], entry["start_date"], entry["end_date"]) == (
            role.title,
            role.org,
            role.start,
            role.end,
        )

    def test_a_role_the_master_cv_lost_is_left_out_and_reported(self, demo_cv):
        document, unresolved = cv_yaml.build(tailored(demo_cv, extra_role="exp-gone"), demo_cv)
        assert unresolved == ["exp-gone"]
        assert len(document["cv"]["sections"]["Experience"]) == 1

    def test_the_file_survives_a_round_trip(self, demo_cv):
        document, _ = cv_yaml.build(tailored(demo_cv, summary="Shipped #rust & fees_owed at 40%."), demo_cv)
        assert cv_yaml.content(cv_yaml.load(cv_yaml.dump(document))) == cv_yaml.content(document)

    def test_blocks_read_like_the_word_file_always_did(self, demo_cv):
        document, _ = cv_yaml.build(tailored(demo_cv), demo_cv)
        blocks = cv_yaml.blocks(document)
        assert [b.text for b in blocks if b.t == "heading"] == ["Summary", "Experience", "Projects", "Education", "Skills"]
        assert {"t": "row", "text": "AI Intern — International Digital Standards Union, Geneva", "right": "2026"} in [
            b.model_dump(exclude_none=True) for b in blocks
        ]
        assert {"t": "row", "text": "BSc Computer Science — University of Westmoor", "right": "2026"} in [
            b.model_dump(exclude_none=True) for b in blocks
        ]
        assert blocks[-1].text == "Python · FastAPI · C#"

    @pytest.mark.parametrize(
        "place,size", [("Toronto, ON, Canada", "us-letter"), ("Austin, TX", "us-letter"), ("London", "a4"), ("Zürich", "a4")]
    )
    def test_paper_follows_the_jobs_country(self, place, size):
        job = Job.model_construct(location=Location(raw=place))
        assert cv_yaml.page_size(job) == size

    def test_justified_themes_never_hyphenate(self):
        for theme in cv_yaml.THEMES:
            design = cv_yaml.default_design(theme)
            alignment = (design.get("typography") or {}).get("alignment")
            assert alignment in (None, "justified-with-no-hyphenation")
        assert cv_yaml.default_design("engineeringresumes")["typography"]["alignment"] == "justified-with-no-hyphenation"
        assert "typography" not in cv_yaml.default_design("opal")  # left-aligned already


class TestLoad:
    def valid(self, demo_cv) -> str:
        return cv_yaml.dump(cv_yaml.build(tailored(demo_cv), demo_cv)[0])

    @pytest.mark.parametrize("key", ["email", "phone", "photo"])
    def test_contact_and_photos_are_refused_where_they_are_written(self, demo_cv, key):
        text = self.valid(demo_cv).replace("  name: Jordan Ellery\n", f"  name: Jordan Ellery\n  {key}: something\n")
        with pytest.raises(cv_yaml.CvFileInvalid) as err:
            cv_yaml.load(text)
        line = text.splitlines().index(f"  {key}: something") + 1
        assert [(i.where, i.line) for i in err.value.issues] == [(f"cv.{key}", line)]

    def test_render_settings_are_wera_s(self, demo_cv):
        text = self.valid(demo_cv) + "settings:\n  render_command:\n    output_folder: /etc\n"
        with pytest.raises(cv_yaml.CvFileInvalid) as err:
            cv_yaml.load(text)
        assert err.value.issues[0].where == "settings"

    def test_renderCV_s_own_errors_come_with_their_line(self, demo_cv):
        text = self.valid(demo_cv).replace("start_date: 2026-02", "start_date: 2026-13", 1)
        with pytest.raises(cv_yaml.CvFileInvalid) as err:
            cv_yaml.load(text)
        issue = err.value.issues[0]
        assert issue.line == [line.strip() for line in text.splitlines()].index("start_date: 2026-13") + 1
        assert issue.message == "The month must be between 1 and 12."
        assert "start_date" in (issue.where or "")

    def test_broken_yaml_says_where(self):
        with pytest.raises(cv_yaml.CvFileInvalid) as err:
            cv_yaml.load("cv:\n  name: [unclosed\n")
        assert err.value.issues[0].line is not None


class TestRender:
    @pytest.mark.parametrize("theme", list(cv_yaml.THEMES))
    def test_every_theme_reads_cleanly_to_a_parser(self, demo_cv, theme):
        """ATSC-05/06 on RenderCV's output: text extracts, headings and every bullet are there
        whole, the contact details are in the header, fonts are embedded and there's no image."""
        doc = tailored(demo_cv)
        document, _ = cv_yaml.build(doc, demo_cv, design=cv_yaml.default_design(theme))
        pdf = cv_pdf.render(document, CONTACT, on=ON)
        assert isinstance(pdf, bytes) and pdf.startswith(b"%PDF")
        text = words(extracted(pdf))
        for heading in ("Summary", "Experience", "Projects", "Education", "Skills"):
            assert heading in text
        for bullet in [b.text for s in doc.sections for i in s.items for b in i.bullets]:
            assert words(bullet) in text.replace("- ", "-")  # a line may break after a hyphen the text has
        for detail in (demo_cv.person.name, CONTACT.email, CONTACT.phone, "jordan-ellery.example"):
            assert detail in text
        assert b"/FontFile2" in pdf or b"/FontFile3" in pdf
        assert b"/Subtype /Image" not in pdf and b"/Subtype/Image" not in pdf
        assert cv_pdf.page_count(pdf) == len(pypdf.PdfReader(io.BytesIO(pdf)).pages) == 1

    def test_no_word_is_split_across_a_line(self, demo_cv):
        """Seven themes justify their text, and Typst hyphenates justified text: with RenderCV's
        defaults this line came out as "internationali- sation" and "heteroge- neous" (18 Nov),
        two tokens to a parser and neither of them the keyword."""
        line = (
            "Led internationalisation of telecommunications microservices, characterising incomprehensibilities and "
            "institutionalisation across heterogeneous multidisciplinary organisations, standardising interoperability"
        )
        doc = tailored(demo_cv, summary=f"{line} {line}")
        doc.sections[0].items[0].bullets[0] = Cited(text=line, source_ids=["exp-x"])
        for theme in cv_yaml.THEMES:
            document, _ = cv_yaml.build(doc, demo_cv, design=cv_yaml.default_design(theme))
            text = words(extracted(cv_pdf.render(document, CONTACT, on=ON)))
            assert text.count(line) == 3, theme

    def test_the_same_pack_renders_the_same_bytes(self, demo_cv):
        """EXP-03: RenderCV stamps the time; WERA fixes it to the approval time."""
        document, _ = cv_yaml.build(tailored(demo_cv), demo_cv)
        assert cv_pdf.render(document, CONTACT, on=ON) == cv_pdf.render(document, CONTACT, on=ON)

    def test_a_number_the_phone_library_rejects_is_printed_as_typed(self, demo_cv):
        # +44 7700 900123 is in Ofcom's drama range, which RenderCV's `phone` field refuses.
        document, _ = cv_yaml.build(tailored(demo_cv), demo_cv)
        assert "+44 7700 900123" in extracted(cv_pdf.render(document, CONTACT, on=ON))

    def test_the_address_stands_in_for_the_cvs_location(self, demo_cv):
        document, _ = cv_yaml.build(tailored(demo_cv), demo_cv)
        out = extracted(cv_pdf.render(document, CONTACT.model_copy(update={"address": "Flat 2, 10 Kew Road"}), on=ON))
        assert "Flat 2, 10 Kew Road" in out

    def test_the_preview_is_page_images(self, demo_cv):
        document, _ = cv_yaml.build(tailored(demo_cv), demo_cv)
        pages = cv_pdf.preview(document, CONTACT, on=ON)
        assert len(pages) == 1 and pages[0].startswith("data:image/png;base64,")


# ---- through the API, as the review screen uses it ----


def ready(api) -> dict:
    return next(a for a in api.ok("GET", "/applications")["items"] if a["status"] == "ready_for_review")


def file_of(api, app_id: str) -> dict:
    return api.ok("GET", f"/applications/{app_id}/cv-file")


class TestTheFileOfAPack:
    def test_a_pack_has_a_file_built_from_it(self, api):
        app = ready(api)
        f = file_of(api, app["id"])
        pack = api.ok("GET", f"/applications/{app['id']}")["pack"]
        assert (f["theme"], f["editedByUser"], f["editable"], len(f["themes"])) == ("engineeringresumes", False, True, 9)
        assert "jordan.ellery@example.com" not in f["yaml"] and "7700" not in f["yaml"]
        bullets = [b["text"] for b in f["blocks"] if b["t"] == "bullet"]
        assert bullets == [b["text"] for s in pack["cv"]["sections"] for i in s["items"] for b in i["bullets"]]
        assert f["name"] == "Jordan Ellery" and f["links"]

    def test_a_line_edited_on_the_review_screen_is_in_the_file(self, api):
        app = ready(api)
        api.ok("PATCH", f"/documents/{app['id']}.cv", {"target": "s0.i0.b0", "text": "Rewritten on the review screen."})
        assert "Rewritten on the review screen." in file_of(api, app["id"])["yaml"]

    def test_a_new_theme_keeps_line_editing(self, api):
        app = ready(api)
        f = file_of(api, app["id"])
        sb2nov = next(t for t in f["themes"] if t["id"] == "sb2nov")
        text = f["yaml"][: f["yaml"].index("design:")] + sb2nov["design"] + "locale:\n  language: english\n"
        saved = api.ok("PUT", f"/applications/{app['id']}/cv-file", {"yaml": text})
        assert (saved["file"]["theme"], saved["file"]["editedByUser"]) == ("sb2nov", False)
        assert saved["application"]["pack"]["cvFile"] == {"theme": "sb2nov", "editedByUser": False}
        api.ok("PATCH", f"/documents/{app['id']}.cv", {"target": "summary", "text": "Still editable line by line."})
        after = file_of(api, app["id"])
        assert after["theme"] == "sb2nov" and "Still editable line by line." in after["yaml"]

    def test_a_rewrite_is_the_cv_until_it_is_discarded(self, api):
        app = ready(api)
        before = api.ok("GET", f"/applications/{app['id']}")["pack"]
        missing = next(k["term"] for k in before["ats"]["keywords"] if not k["matched"])
        f = file_of(api, app["id"])
        summary = next(b["text"] for b in f["blocks"] if b["t"] == "body")
        text = f["yaml"].replace(summary, f"{summary} Also {missing}.", 1)
        saved = api.ok("PUT", f"/applications/{app['id']}/cv-file", {"yaml": text})
        assert saved["file"]["editedByUser"] is True and saved["file"]["yaml"] == text
        # The ATS report reads the file now, so the term the rewrite added is matched.
        assert next(k for k in saved["application"]["pack"]["ats"]["keywords"] if k["term"] == missing)["matched"]
        # The pack's own draft is untouched, and line editing waits.
        assert saved["application"]["pack"]["cv"] == before["cv"]
        assert api.code("PATCH", f"/documents/{app['id']}.cv", {"target": "summary", "text": "x"}) == "CV_FILE_EDITED"
        assert api.code("PATCH", f"/documents/{app['id']}.letter", {"target": "p0", "text": "The letter still edits."}) == "OK"

        reset = api.ok("PUT", f"/applications/{app['id']}/cv-file", {"reset": True})
        assert reset["file"]["editedByUser"] is False and f"Also {missing}." not in reset["file"]["yaml"]
        assert api.code("PATCH", f"/documents/{app['id']}.cv", {"target": "summary", "text": "Editable again."}) == "OK"

    def test_the_page_count_is_the_pdfs(self, api):
        app = ready(api)
        saved = api.ok("PUT", f"/applications/{app['id']}/cv-file", {"yaml": file_of(api, app["id"])["yaml"]})
        pages = next(c for c in saved["application"]["pack"]["ats"]["formatChecks"] if c["id"] == "pages")
        assert (saved["application"]["pack"]["ats"]["pages"], pages["message"]) == (1, "1 page")

    def test_a_file_that_wont_render_is_refused_with_its_line(self, api):
        app = ready(api)
        text = file_of(api, app["id"])["yaml"].replace(
            "  name: Jordan Ellery\n", "  name: Jordan Ellery\n  email: me@example.com\n"
        )
        status, body = api("PUT", f"/applications/{app['id']}/cv-file", {"yaml": text})
        assert (status, body["error"]["code"]) == (422, "CV_FILE_INVALID")
        line = text.splitlines().index("  email: me@example.com") + 1
        assert body["error"]["fields"] == {f"line {line}": cv_yaml.REFUSED["email"]}
        assert file_of(api, app["id"])["editedByUser"] is False  # nothing was saved

    def test_the_preview_answers_while_the_person_types(self, api):
        app = ready(api)
        f = file_of(api, app["id"])
        good = api.ok("POST", f"/applications/{app['id']}/cv-file/preview", {"yaml": f["yaml"]})
        assert good["issues"] == [] and good["pages"][0].startswith("data:image/png;base64,")
        half = api.ok(
            "POST",
            f"/applications/{app['id']}/cv-file/preview",
            {"yaml": f["yaml"].replace("start_date: 20", "start_date: 9", 1)},
        )
        assert half["pages"] == [] and half["issues"][0]["line"]

    def test_the_pdf_carries_the_contact_details_and_never_changes(self, api, client):
        app = ready(api)
        a = client.get(f"/applications/{app['id']}/cv.pdf")
        assert a.headers["content-type"] == "application/pdf" and a.content.startswith(b"%PDF")
        assert "jordan.ellery@example.com" in extracted(a.content)
        assert client.get(f"/applications/{app['id']}/cv.pdf").content == a.content

    def test_an_approved_pack_is_frozen(self, api, client):
        app = ready(api)
        f = file_of(api, app["id"])
        api.ok("POST", f"/applications/{app['id']}/approve")
        approved = file_of(api, app["id"])
        assert approved["editable"] is False
        assert api.code("PUT", f"/applications/{app['id']}/cv-file", {"yaml": f["yaml"]}) == "INVALID_STATE"
        # EXP-03: dated by the approval, so a download next week is the same file.
        assert client.get(f"/applications/{app['id']}/cv.pdf").content == client.get(f"/applications/{app['id']}/cv.pdf").content

    def test_a_redraft_replaces_a_rewrite_and_keeps_the_design(self, api):
        app = ready(api)
        f = file_of(api, app["id"])
        harvard = next(t for t in f["themes"] if t["id"] == "harvard")
        text = f["yaml"][: f["yaml"].index("design:")] + harvard["design"] + "locale:\n  language: english\n"
        summary = next(b["text"] for b in f["blocks"] if b["t"] == "body")
        text = text.replace(summary, f"My own opening. {summary}", 1)
        assert api.ok("PUT", f"/applications/{app['id']}/cv-file", {"yaml": text})["file"]["editedByUser"] is True
        api.ok("POST", f"/applications/{app['id']}/regenerate", {"note": "Shorter"})
        stored = api.ok("GET", f"/applications/{app['id']}")
        assert stored["status"] == "drafting"
        # Drafting hides the pack; the file's state is what the redraft will be read with.
        from wera_agent.db.session import transaction
        from wera_agent.services.store import Store

        with transaction() as session:
            row = Store(session)._cv_file_row(app["id"])
        assert row is not None and (row["theme"], row["editedByUser"]) == ("harvard", False)


class TestTheWebAppsFixture:
    """The web app lints the CV PDF and writes the Word file in TypeScript, from what the agent
    sends, and its tests can't run RenderCV. So they read a file rendered here, from the pack
    their own tests use (`fixtures/demo/tailored-cv.json`), and this test fails when that render
    would come out differently. Regenerate with `WERA_WRITE_FIXTURES=1 pytest -k web_apps_fixture`.

    Text, not bytes: a RenderCV or font update that moves nothing a reader sees shouldn't fail it.
    """

    def test_the_web_apps_fixture_is_this_render(self, demo_cv):
        out = FIXTURES_DIR / "demo" / "rendercv"
        doc = TailoredCV.model_validate_json((FIXTURES_DIR / "demo" / "tailored-cv.json").read_text())
        contact = Contact.model_validate_json((FIXTURES_DIR / "demo" / "contact.json").read_text())
        document, unresolved = cv_yaml.build(doc, demo_cv)
        name, location, links = cv_yaml.header(document)
        file = CvFile(
            yaml=cv_yaml.dump(document),
            theme=cv_yaml.DEFAULT_THEME,
            page_size="a4",
            edited_by_user=False,
            editable=False,
            themes=cv_yaml.themes("a4"),
            name=name,
            location=location,
            links=links,
            blocks=cv_yaml.blocks(document),
            unresolved_role_ids=unresolved,
        ).model_dump(mode="json")
        pdf = cv_pdf.render(document, contact, on=ON)
        assert isinstance(pdf, bytes)
        if os.environ.get("WERA_WRITE_FIXTURES"):
            out.mkdir(parents=True, exist_ok=True)
            (out / "cv-file.json").write_text(json.dumps(file, indent=2, ensure_ascii=False) + "\n")
            (out / "cv.pdf").write_bytes(pdf)
        hint = "Regenerate with WERA_WRITE_FIXTURES=1 pytest -k web_apps_fixture"
        assert json.loads((out / "cv-file.json").read_text()) == file, hint
        assert words(extracted((out / "cv.pdf").read_bytes())) == words(extracted(pdf)), hint


class TestConcurrency:
    def test_parallel_requests_never_share_a_parser(self, demo_cv):
        """RenderCV reads YAML through one module-level ruamel parser, which isn't thread-safe. In
        the e2e run on 18 Nov a line edit and the review rail's format check read at the same
        moment, and both failed (IndexError, ParserError). Every read goes through one lock."""
        from concurrent.futures import ThreadPoolExecutor

        text = cv_yaml.dump(cv_yaml.build(tailored(demo_cv), demo_cv)[0])

        def work(i: int) -> str:
            if i % 3 == 0:
                cv_yaml._justified.cache_clear()
                cv_yaml.default_design()
            return cv_yaml.content(cv_yaml.load(text))

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(work, range(64)))
        assert len(set(results)) == 1
