"""International organisations' own vacancy systems (SRCH-34).

The UN Secretariat, WHO, WIPO, WTO, IMF and ITU publish their vacancies on four platforms, and
each is read the way its own public careers page reads it — checked by hand on 16 Nov 2026:

* **un_careers** (careers.un.org): the RSS feed `/jobfeed` lists every opening with its level,
  job family, department, duty station and deadline; `/api/public/opening/joV2/<id>/en` is the
  posting itself, the call its job page makes.
* **taleo** (WHO, WIPO): `careersection/rest/jobboard/searchjobs`, the call the job board page
  makes, lists every requisition 25 at a time; the posting's own page carries its text in a form
  field (`initialHistory`). Its RSS feed gives only the ten newest, with no duty station.
* **workday** (WTO, IMF): `/wday/cxs/<tenant>/<site>/jobs`, then the posting by its path.
* **successfactors** (ITU): the server-rendered `/search/` page, then each `/job/` page. ITU's
  robots.txt disallows `/services/`, where its RSS lives, so the RSS is not read.

**Two steps, so a posting costs a request only when it could be shown.** An organisation's list is
one request (or a few pages) and carries everything MATCH-01 judges — title, duty station, dates,
usually the grade. Only a posting that passes the profile's filters is read in full, at most
`max_details` a run across every organisation, newest first. A posting that doesn't pass is not
saved at all: an organisation's list is its whole catalogue (the UN's is ~460, mostly senior posts
and consultancies), saved without its text it could never come back when the profile changes, and
it is listed again the next day and read then if it passes. Every listed id is still reported
(`FeedResult.listed`), so ATS-03 closes only what left a list that answered.
"""

import contextlib
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from string import capwords
from typing import Any

import logfire

from ..config import Organisation
from ..domain import grades
from ..http import request
from ..schemas.jobs import Ats, InternationalPost, Location
from .ats_feeds import FeedJob, FeedResult, html_to_text, remote_mode
from .robots import Robots


@dataclass
class Listed:
    """One opening as its organisation's list gives it, before its full text is read."""

    job_id: str
    title: str
    url: str  # the public posting page, which is also where the person applies
    location: str  # as "City, Country", several joined with " | "
    posted_at: str | None = None
    closes_at: str | None = None
    grade: str | None = None  # as written: "P3", "NOD", "No grade"
    recruitment: str | None = None  # as stated: "International", "Local"
    family: str | None = None
    department: str | None = None
    snippet: str = ""
    handle: str | None = None  # what the platform needs to fetch the posting (Workday's path)


class Unreadable(Exception):
    """The organisation answered with something its reader doesn't recognise."""


# ---------------------------------------------------------------------------
# UN Careers


def _un_list(org: Organisation) -> list[Listed]:
    body = _get(org, f"https://{org.host}/jobfeed").text
    out: list[Listed] = []
    for item in ET.fromstring(body).iter("item"):
        link = (item.findtext("link") or "").strip()
        found = re.search(r"/jobSearchDescription/(\d+)", link)
        if not found:
            continue
        fields = dict(re.findall(r"([A-Za-z/ ]+?)\s*:\s*([^<]*?)\s*(?:<br>|$)", item.findtext("description") or ""))
        fields = {k.strip(): v.strip() for k, v in fields.items()}
        station = fields.get("Duty Station", "")
        out.append(
            Listed(
                job_id=found.group(1),
                title=" ".join((item.findtext("title") or "").replace("​", "").split()),
                url=link,
                location=capwords(station.lower()) if station.isupper() else station,
                posted_at=_us_date(fields.get("Posted Date")),
                closes_at=_us_date(fields.get("Deadline")),
                grade=fields.get("Level") or None,
                family=fields.get("Job Family") or None,
                department=fields.get("Department/Office") or None,
            )
        )
    return out


def _un_detail(org: Organisation, item: Listed) -> str | None:
    data = _get(org, f"https://{org.host}/api/public/opening/joV2/{item.job_id}/en").json().get("data") or {}
    return html_to_text(data.get("jobDescription")) or None


def _us_date(raw: str | None) -> str | None:
    """'10/23/2026 00:00:00 AM (New York time)' → '2026-10-23'."""
    found = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", raw or "")
    return f"{found.group(3)}-{int(found.group(1)):02d}-{int(found.group(2)):02d}" if found else None


# ---------------------------------------------------------------------------
# Taleo

_TALEO_PAGE = {
    "multilineEnabled": True,
    "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
    "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
    "filterSelectionParam": {"searchFilterSelections": []},
    "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
}
# Its list rejects a request without a time zone ("An Error Occurred in TEE", a 500).
_TALEO_HEADERS = {"Content-Type": "application/json", "tz": "GMT+00:00", "tzname": "Europe/London"}
_GRADE_CELL = re.compile(r"^(?:(?:P|D|NO|G|GS|FS)\s?-?[0-9A-E]|No grade)$", re.I)
_NUMBER_CELL = re.compile(r"^\d[0-9A-Z_-]{3,}$")


def _taleo_list(org: Organisation) -> list[Listed]:
    out: list[Listed] = []
    url = f"https://{org.host}/careersection/rest/jobboard/searchjobs?lang=en&portal={org.portal}"
    for page in range(1, 41):  # 1,000 posts at most; WHO lists 55
        body = _post(org, url, json={**_TALEO_PAGE, "pageNo": page}, headers=_TALEO_HEADERS).json()
        rows = body.get("requisitionList") or []
        out += [listed for row in rows if (listed := _taleo_row(org, row)) is not None]
        paging = body.get("pagingData") or {}
        if not rows or page * int(paging.get("pageSize") or 25) >= int(paging.get("totalCount") or 0):
            break
    return out


def _taleo_row(org: Organisation, row: dict[str, Any]) -> Listed | None:
    """A requisition's columns. Each career section orders them its own way (WHO puts the job
    number second and the grade fourth, WIPO the grade second), so a cell is read by what it
    holds, not by where it sits."""
    cells = [str(c).strip() for c in row.get("column") or []]
    if not cells or not cells[0]:
        return None
    title, rest = cells[0], cells[1:]
    places: list[str] = []
    number = grade = recruitment = closes = None
    for cell in rest:
        if cell.startswith("["):
            with contextlib.suppress(ValueError):
                places = [_taleo_place(p) for p in json.loads(cell)]
        elif _GRADE_CELL.match(cell) and grade is None:
            grade = cell
        elif cell in ("International", "Local") and recruitment is None:
            recruitment = cell
        elif (when := _taleo_date(cell)) is not None:
            closes = when
        elif _NUMBER_CELL.match(cell) and number is None:
            number = cell
    if number is None:
        return None
    return Listed(
        job_id=number,
        title=title,
        url=f"https://{org.host}/careersection/{org.section}/jobdetail.ftl?lang=en&job={urllib.parse.quote(number)}",
        location=" | ".join(p for p in places if p),
        closes_at=closes,
        grade=grade,
        recruitment=recruitment,
    )


def _taleo_place(raw: str) -> str:
    """'Tanzania, United Republic of-Dar-es-Salaam' → 'Dar-es-Salaam, Tanzania, United Republic
    of'; WIPO's 'Switzerland-CH-Geneva' → 'Geneva, Switzerland'. Country first, then the city,
    which may itself carry hyphens."""
    country, _, city = raw.partition("-")
    city = re.sub(r"^[A-Z]{2}-", "", city.strip())
    return f"{city}, {country.strip()}" if city else country.strip()


def _taleo_date(raw: str) -> str | None:
    """'Nov 29, 2026, 10:59:00 PM' or '16-Dec-2026, 10:59:00 PM' → '2026-11-29'."""
    for pattern in ("%b %d, %Y", "%d-%b-%Y"):
        head = raw.split(", ")[0] if pattern == "%d-%b-%Y" else ", ".join(raw.split(", ")[:2])
        try:
            return datetime.strptime(head, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _taleo_detail(org: Organisation, item: Listed) -> str | None:
    """The posting's text, from the form field its page fills itself from. Parts that open with
    `!*!` are the description's sections; the dates are the posting's own (the first is when it
    was posted, the list having given only the deadline)."""
    page = _get(org, item.url).text
    field = re.search(r'id="initialHistory"[^>]*value="([^"]*)"', page)
    if not field:
        raise Unreadable("no initialHistory field")
    parts = [urllib.parse.unquote(p.replace("%5C:", ":")) for p in html.unescape(field.group(1)).split("!|!")]
    dates = sorted(d for p in parts if (d := _taleo_date(p)) is not None)
    if dates and not item.posted_at:
        item.posted_at = dates[0]
    sections = [html_to_text(p.removeprefix("!*!")) for p in parts if p.startswith("!*!")]
    return "\n\n".join(s for s in sections if s) or None


# ---------------------------------------------------------------------------
# Workday


def _workday_list(org: Organisation) -> list[Listed]:
    out: list[Listed] = []
    base = f"https://{org.host}/wday/cxs/{org.tenant}/{org.site}"
    for offset in range(0, 1000, 20):
        body = _post(org, f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""}).json()
        rows = body.get("jobPostings") or []
        for row in rows:
            path = row.get("externalPath") or ""
            bullets = [str(b) for b in row.get("bulletFields") or []]
            deadline = next((re.search(r"(\d{2})/(\d{2})/(\d{4})", b) for b in bullets if "apply" in b.lower()), None)
            out.append(
                Listed(
                    job_id=(bullets[0] if bullets else path.rsplit("_", 1)[-1]),
                    title=str(row.get("title") or "").strip(),
                    url=f"https://{org.host}/{org.site}{path}",
                    location=str(row.get("locationsText") or ""),
                    posted_at=_workday_posted(row.get("postedOn")),
                    closes_at=f"{deadline.group(3)}-{deadline.group(1)}-{deadline.group(2)}" if deadline else None,
                    handle=path,
                )
            )
        if not rows or offset + 20 >= int(body.get("total") or 0):
            break
    return out


def _workday_posted(raw: object) -> str | None:
    """'Posted Today', 'Posted 3 Days Ago' → a date; 'Posted 30+ Days Ago' → unknown until the
    posting itself is read (its `startDate`)."""
    text = str(raw or "").lower()
    today = datetime.now(UTC).date()
    if "today" in text:
        return today.isoformat()
    if "yesterday" in text:
        return (today - timedelta(days=1)).isoformat()
    found = re.search(r"(\d+)\s+days? ago", text)
    return (today - timedelta(days=int(found.group(1)))).isoformat() if found and "+" not in text else None


def _workday_detail(org: Organisation, item: Listed) -> str | None:
    info = _get(org, f"https://{org.host}/wday/cxs/{org.tenant}/{org.site}{item.handle}").json().get("jobPostingInfo") or {}
    item.posted_at = info.get("startDate") or item.posted_at
    item.closes_at = info.get("endDate") or item.closes_at
    country = ((info.get("jobRequisitionLocation") or {}).get("country") or {}).get("descriptor")
    if country and country.lower() not in item.location.lower():
        item.location = f"{item.location}, {country}" if item.location else country
    return html_to_text(info.get("jobDescription")) or None


# ---------------------------------------------------------------------------
# SuccessFactors


def _sf_list(org: Organisation) -> list[Listed]:
    out: list[Listed] = []
    for start in range(0, 1000, 25):
        page = _get(org, f"https://{org.host}/search/?q=&locale=en_US&startrow={start}").text
        rows = re.findall(r'<tr class="data-row[^"]*">(.*?)</tr>', page, re.S)
        for row in rows:
            link = re.search(r'href="(/job/[^"]+/(\d+)/)"', row)
            if not link:
                continue
            out.append(
                Listed(
                    job_id=link.group(2),
                    title=_cell(row, "jobTitle-link") or "",
                    url=f"https://{org.host}{link.group(1)}",
                    location=_cell(row, "jobLocation") or "",
                    posted_at=_sf_date(_cell(row, "jobDate")),
                )
            )
        total = re.search(r"of\s*<b>(\d+)</b>", page)
        if not rows or start + 25 >= int(total.group(1) if total else 0):
            break
    return out


def _cell(row: str, css: str) -> str | None:
    found = re.search(rf'class="{css}"[^>]*>(.*?)</(?:a|span)>', row, re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", found.group(1))).split()) if found else None


def _sf_date(raw: str | None) -> str | None:
    try:
        return datetime.strptime((raw or "").strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def _sf_detail(org: Organisation, item: Listed) -> str | None:
    from selectolax.lexbor import LexborHTMLParser

    page = _get(org, item.url).text
    node = LexborHTMLParser(page).css_first('[itemprop="description"]')
    text = html_to_text(node.html) if node is not None else ""
    if grade := re.search(r"\bGrade:\s*([A-Z]{1,2}-?\d)", text):
        item.grade = grade.group(1)
    if deadline := re.search(r"Application deadline[^:]*:\s*(\d{1,2} \w+ \d{4})", text):
        with contextlib.suppress(ValueError):
            item.closes_at = datetime.strptime(deadline.group(1), "%d %B %Y").date().isoformat()
    return text or None


# ---------------------------------------------------------------------------
# One posting, and every organisation

LISTS: dict[str, Callable[[Organisation], list[Listed]]] = {
    "un_careers": _un_list,
    "taleo": _taleo_list,
    "workday": _workday_list,
    "successfactors": _sf_list,
}
DETAILS: dict[str, Callable[[Organisation, Listed], str | None]] = {
    "un_careers": _un_detail,
    "taleo": _taleo_detail,
    "workday": _workday_detail,
    "successfactors": _sf_detail,
}


def to_feed(org: Organisation, item: Listed, description: str) -> FeedJob:
    """A posting as the watchlist carries any employer's: the organisation's own fields."""
    grade = grades.parse(item.grade) or grades.parse(item.title)
    first = item.location.split(" | ")[0]
    parts = [p.strip() for p in first.split(",") if p.strip()]
    # The UN's department is the employer people know ("United Nations Office at Geneva", "Office
    # for the Coordination of Humanitarian Affairs"); the organisation's name goes first unless the
    # department already says it.
    dept = (item.department or "").strip()
    company = dept if dept.lower().startswith(org.name.lower()) else f"{org.name}, {dept}" if dept else org.name
    return FeedJob(
        ats=Ats(vendor=org.platform, board_token=org.id, job_id=item.job_id),
        title=item.title,
        company=company,
        location=Location(
            city=parts[0] if parts else None, country=parts[-1] if len(parts) > 1 else None, raw=item.location or "Not stated"
        ),
        apply_url=item.url,
        description=description,
        posted_at=item.posted_at,
        remote=remote_mode(item.location if re.search(r"home[\s-]?based|remote", item.location, re.I) else ""),
        closes_at=item.closes_at,
        international=InternationalPost(
            org=org.id,
            grade=grade.code if grade else None,
            recruitment=grades.recruitment(item.title, grade, item.recruitment),
            family=item.family,
        ),
    )


def fetch_all(
    orgs: list[Organisation], *, wanted: Callable[[FeedJob], bool], max_details: int, robots: Robots | None = None
) -> FeedResult:
    """Every organisation's list, then the full text of the postings `wanted` keeps.

    `wanted` is the profile's MATCH-01 filters, judged on the list's own fields. An organisation
    that doesn't answer costs its own postings this run and nothing else; one that answers is
    in `answered`, with every id it listed in `listed`.
    """
    result = FeedResult()
    robots = robots or Robots()
    keep: list[tuple[Organisation, Listed]] = []
    for org in orgs:
        try:
            items = LISTS[org.platform](_checked(org, robots))
        except PermissionError as refused:
            why = (
                "couldn't be read, so it was left alone this run"
                if str(refused) == "unreadable"
                else "disallows reading its vacancies"
            )
            result.errors.append(f"{org.name}: its robots.txt {why}")
            continue
        except Exception as exc:  # noqa: BLE001 — one organisation down is not the run failing
            logfire.warn("{org} vacancies didn't answer", org=org.name)
            result.errors.append(f"{org.name}: its vacancies didn't answer ({type(exc).__name__})")
            continue
        result.answered.add(f"{org.platform}:{org.id}")
        seen: set[str] = set()
        for item in items:
            if item.job_id in seen:
                continue  # a list that pages can repeat a row across a page boundary
            seen.add(item.job_id)
            result.listed.add(f"{org.platform}:{org.id}:{item.job_id}")
            if wanted(to_feed(org, item, item.snippet)):
                keep.append((org, item))
    # Newest first, so the cap leaves the oldest for the next run; undated ones last.
    keep.sort(key=lambda pair: pair[1].posted_at or "", reverse=True)
    for org, item in keep[:max_details]:
        try:
            if not robots.allows(item.url):
                continue
            text = DETAILS[org.platform](org, item)
        except Exception as exc:  # noqa: BLE001 — a posting that won't open is skipped, not fatal
            logfire.warn("{org} posting {job} couldn't be read: {why}", org=org.name, job=item.job_id, why=type(exc).__name__)
            continue
        if text:
            result.jobs.append(to_feed(org, item, text))
    if len(keep) > max_details:
        logfire.info("{n} organisation posting(s) left for the next run", n=len(keep) - max_details)
    return result


def _checked(org: Organisation, robots: Robots) -> Organisation:
    """Refuse an organisation whose robots.txt says no to its list, before asking it anything."""
    probe = {
        "un_careers": f"https://{org.host}/jobfeed",
        "taleo": f"https://{org.host}/careersection/rest/jobboard/searchjobs",
        "workday": f"https://{org.host}/wday/cxs/{org.tenant}/{org.site}/jobs",
        "successfactors": f"https://{org.host}/search/",
    }[org.platform]
    verdict = robots.verdict(probe)
    if verdict != "allowed":
        raise PermissionError(verdict)
    return org


def _get(org: Organisation, url: str):  # noqa: ANN202 — an httpx.Response
    response = request("GET", url)
    response.raise_for_status()
    return response


def _post(org: Organisation, url: str, **kwargs: Any):  # noqa: ANN202
    response = request("POST", url, **kwargs)
    response.raise_for_status()
    return response
