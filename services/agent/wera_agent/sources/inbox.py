"""The alert inbox (MAIL-01…10, T-03, T-08).

This is the most hostile input WERA accepts. Anyone can send an email, a job ad is a natural
place to hide an instruction aimed at a model, and an email body is full of tracking links and
images that leak the fact it was opened. So the rules here are refusals, in order:

* **A dedicated mailbox, one folder, read-only.** `EXAMINE` and `BODY.PEEK` only, so the
  person's own unread flags never move (MAIL-02, T-08).
* **Allowlisted sender *and* an aligned DKIM signature** (MAIL-03). The allowlist alone is
  worthless — a `From:` header is free to forge — so the signature has to come from the domain
  it claims. Anything else is counted as `rejected_unauthenticated` and never parsed.
* **Nothing is fetched, ever** (MAIL-06). No images, no tracking links, no attachments. Links
  become tokens before any model sees them, and a LinkedIn URL is canonicalised and stored for
  the person to open themselves (MAIL-07).
* **A deterministic parser first** (MAIL-04). The fast model is the fallback for a layout no
  parser knows, and it is asked only for cards — it has no tools, so the worst a buried
  instruction can do is produce output that fails validation (T-03).
* **The body is discarded** once cards are out of it (MAIL-08).
"""

import email
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any

import logfire

from ..config import Sender
from ..domain.text import is_linkedin_url

MAX_BYTES = 2 * 1024 * 1024  # MAIL-10
FIRST_RUN_DAYS = 14  # MAIL-09

LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:jobs/view|comm/jobs/view)/(?P<id>\d+)", re.I)
INDEED_JOB = re.compile(r"indeed\.com/(?:viewjob|rc/clk|pagead/clk)", re.I)


@dataclass
class Card:
    """One job spotted in an email, before anything is resolved."""

    title: str
    company: str
    location_raw: str
    url: str | None  # a canonical job URL, or None
    linkedin_url: str | None = None


@dataclass
class MessageMeta:
    """What WERA keeps about an email (MAIL-08). The body is not here on purpose."""

    message_id: str
    sender: str
    received_at: str | None
    uid: int


@dataclass
class InboxResult:
    cards: list[tuple[MessageMeta, Card]] = field(default_factory=list)
    checked: int = 0
    rejected_unauthenticated: int = 0
    skipped_too_large: int = 0
    parsed_by_model: int = 0
    errors: list[str] = field(default_factory=list)
    last_uid: int | None = None


# ---------------------------------------------------------------------------
# Sender authentication (MAIL-03)


def sender_domain(from_header: str) -> str:
    """The domain an email claims to be from. Claims only — hence the signature check."""
    match = re.search(r"[\w.+-]+@([\w.-]+)", from_header or "")
    return (match.group(1) if match else "").lower().strip(".")


def allowed_sender(domain: str, senders: list[Sender]) -> Sender | None:
    for sender in senders:
        if domain == sender.domain or domain.endswith(f".{sender.domain}"):
            return sender
    return None


def signing_domains(raw: bytes) -> set[str]:
    """The `d=` of every DKIM-Signature header, read with the email parser rather than by
    scanning lines — a signature header is folded across lines and there can be several."""
    try:
        headers = email.message_from_bytes(raw).get_all("DKIM-Signature") or []
    except Exception:  # noqa: BLE001 — an unparsable message has no signatures
        return set()
    found: set[str] = set()
    for header in headers:
        match = re.search(r"(?:^|;)\s*d\s*=\s*([^;\s]+)", str(header))
        if match:
            found.add(match.group(1).lower().strip("."))
    return found


def _aligned(signed: str, claimed: str) -> bool:
    """Relaxed alignment: a subdomain signature counts, an unrelated domain does not."""
    return signed == claimed or signed.endswith(f".{claimed}") or claimed.endswith(f".{signed}")


def dkim_aligned(raw: bytes, domain: str) -> bool:
    """DKIM verifies **and** the signing domain is aligned with the sender's (MAIL-03).

    Alignment is the part that matters. A valid signature from `mailer.example.com` on an email
    claiming to be from `linkedin.com` proves only that someone owns a mail server.
    """
    try:
        import dkim
    except ImportError:  # pragma: no cover — the dependency is declared
        return False
    try:
        if not dkim.verify(raw):
            return False
    except Exception:  # noqa: BLE001 — an unverifiable signature is a failed one
        return False
    return any(_aligned(signed, domain) for signed in signing_domains(raw))


# ---------------------------------------------------------------------------
# Reading the body without fetching anything (MAIL-06)


def plain_text(message: Message) -> str:
    """The text of an email, with no remote content of any kind.

    HTML parts are reduced to text locally. `<img>` is dropped rather than resolved, so opening
    an alert never tells the sender it was read.
    """
    parts: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue  # MAIL-06: attachments are never opened
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — an undecodable part is skipped, not fatal
            continue
        parts.append(_html_to_text(body) if kind == "text/html" else body)
    return "\n".join(parts)


def _html_to_text(html_body: str, tokens: dict[str, str] | None = None) -> str:
    """HTML reduced to text, locally, with nothing loaded.

    The lexbor backend, not the Modest one: selectolax 1.0 dropped Modest for being eight years
    unmaintained and not HTML5-compliant.

    When `tokens` is given, each link is annotated inline as `text [[URL_n]]`. That matters for
    the fallback model: a URL lives in an `href`, so stripping tags leaves "View job" with
    nothing to tie it to a posting. The token sits next to the title the model is reading, and
    comes back as the one thing the model is trusted to return about a link (MAIL-05).
    """
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(html_body)
    for tag in tree.css("script, style, img"):  # MAIL-06
        tag.decompose()
    if tokens is not None:
        by_link = {link: token for token, link in tokens.items()}
        for anchor in tree.css("a"):
            href = anchor.attributes.get("href")
            token = by_link.get(href or "")
            if token:
                anchor.insert_after(f" {token} ")
    text = tree.body.text(separator="\n") if tree.body else ""
    return re.sub(r"\n{3,}", "\n\n", text)


def links_in(message: Message) -> list[str]:
    """Every href in the email, unfollowed. Tracking wrappers are left exactly as they are —
    WERA reads them, it never requests them (MAIL-06)."""
    found: list[str] = []
    for part in message.walk():
        if part.get_content_type() != "text/html" or part.get_filename():
            continue
        try:
            body = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        from selectolax.lexbor import LexborHTMLParser

        for node in LexborHTMLParser(body).css("a"):
            href = node.attributes.get("href")
            if href:
                found.append(href)
    return found


def canonical_linkedin(url: str) -> str | None:
    """MAIL-07: `/jobs/view/<id>`, tracking stripped. Stored, never fetched."""
    match = LINKEDIN_JOB.search(url or "")
    return f"https://www.linkedin.com/jobs/view/{match.group('id')}" if match else None


def annotated_text(message: Message, tokens: dict[str, str]) -> str:
    """The email's text with each link replaced by its token, in place."""
    parts: list[str] = []
    for part in message.walk():
        if part.get_filename() or part.get_content_type() not in ("text/plain", "text/html"):
            continue
        try:
            body = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        parts.append(_html_to_text(body, tokens) if part.get_content_type() == "text/html" else body)
    return "\n".join(parts)


def tokenise_links(text: str, links: list[str], mapping: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """MAIL-05: URLs become `[[URL_1]]` before any model sees the email.

    A model that can't see a URL can't be talked into asking for one, and the token is what
    comes back, so WERA maps it to a link it already had.
    """
    if mapping is None:
        mapping = {f"[[URL_{i}]]": link for i, link in enumerate(dict.fromkeys(links), start=1)}
    cleaned = text
    for token, link in mapping.items():
        cleaned = cleaned.replace(link, token)
    # Anything still URL-shaped is replaced too, so none reach the prompt.
    cleaned = re.sub(r"https?://\S+", "[[URL]]", cleaned)
    return cleaned, mapping


# ---------------------------------------------------------------------------
# Deterministic parsers (MAIL-04, MAIL-05)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" ·-–—‌ ")


def parse_linkedin(text: str, links: list[str]) -> list[Card]:
    """LinkedIn alerts list "Title / Company / Location" blocks above a job link."""
    jobs = [link for link in links if canonical_linkedin(link)]
    cards: list[Card] = []
    lines = [_clean(line) for line in text.split("\n")]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines):
        # A title line is followed by a company line and usually a location line.
        if index + 1 >= len(lines) or len(line) > 120 or not re.search(r"[a-z]", line):
            continue
        following = lines[index + 1]
        if not following or len(following) > 80 or following.lower().startswith(("view job", "see all", "unsubscribe")):
            continue
        looks_like_job = re.search(r"engineer|developer|scientist|analyst|manager|designer|intern|graduate", line, re.I)
        if not looks_like_job:
            continue
        location = lines[index + 2] if index + 2 < len(lines) else ""
        url = jobs[len(cards)] if len(cards) < len(jobs) else None
        canonical = canonical_linkedin(url or "")
        cards.append(
            Card(
                title=line,
                company=following,
                location_raw=location if len(location) < 60 else "",
                url=None,  # MAIL-07: never the apply URL for LinkedIn
                linkedin_url=canonical,
            )
        )
    return cards


def parse_indeed(text: str, links: list[str]) -> list[Card]:
    """Indeed alerts put the company and location on one line under the title."""
    cards: list[Card] = []
    job_links = [link for link in links if INDEED_JOB.search(link)]
    lines = [_clean(line) for line in text.split("\n")]
    lines = [line for line in lines if line]
    for index, line in enumerate(lines):
        if index + 1 >= len(lines) or len(line) > 120:
            continue
        if not re.search(r"engineer|developer|scientist|analyst|manager|designer|intern|graduate", line, re.I):
            continue
        detail = lines[index + 1]
        # "Fernhill AI - London, UK" or "Fernhill AI, London"
        match = re.match(r"^(?P<company>.{2,60}?)\s*(?:-|–|,)\s*(?P<where>.{2,60})$", detail)
        if not match:
            continue
        cards.append(
            Card(
                title=line,
                company=_clean(match.group("company")),
                location_raw=_clean(match.group("where")),
                url=job_links[len(cards)] if len(cards) < len(job_links) else None,
            )
        )
    return cards


PARSERS = {"linkedin": parse_linkedin, "indeed": parse_indeed}


def parse_cards(sender: Sender, text: str, links: list[str]) -> tuple[list[Card], bool]:
    """`(cards, used_parser)`. An empty result means the fallback model is worth asking."""
    parser = PARSERS.get(sender.parser)
    if parser is None:
        return [], False
    cards = parser(text, links)
    return cards, bool(cards)


# ---------------------------------------------------------------------------
# One message, end to end


def message_meta(message: Message, uid: int) -> MessageMeta:
    received = None
    try:
        raw_date = message.get("Date")
        received = parsedate_to_datetime(raw_date).isoformat() if raw_date else None
    except (TypeError, ValueError):
        received = None
    return MessageMeta(
        message_id=(message.get("Message-ID") or f"uid-{uid}").strip("<> "),
        sender=sender_domain(message.get("From") or ""),
        received_at=received,
        uid=uid,
    )


def read_message(
    raw: bytes,
    uid: int,
    senders: list[Sender],
    *,
    extract: Any = None,
) -> tuple[list[tuple[MessageMeta, Card]], str | None]:
    """Cards from one email, or a reason it produced none.

    `extract` is the fallback used when no parser recognises the layout: a callable taking the
    tokenised text and returning card dicts. It is injected so this function never reaches the
    network itself.
    """
    if len(raw) > MAX_BYTES:
        return [], "too_large"  # MAIL-10

    message = email.message_from_bytes(raw)
    domain = sender_domain(message.get("From") or "")
    sender = allowed_sender(domain, senders)
    if sender is None:
        return [], "sender_not_allowed"
    if not dkim_aligned(raw, sender.domain):
        return [], "rejected_unauthenticated"  # MAIL-03

    meta = message_meta(message, uid)
    text = plain_text(message)
    links = links_in(message)

    cards, used_parser = parse_cards(sender, text, links)
    if not used_parser and extract is not None:
        mapping = {f"[[URL_{i}]]": link for i, link in enumerate(dict.fromkeys(links), start=1)}
        tokenised, mapping = tokenise_links(annotated_text(message, mapping), links, mapping)
        try:
            for raw_card in extract(tokenised) or []:
                token = raw_card.get("urlToken")
                url = mapping.get(token or "", None)
                cards.append(
                    Card(
                        title=_clean(str(raw_card.get("title") or "")),
                        company=_clean(str(raw_card.get("company") or "")),
                        location_raw=_clean(str(raw_card.get("locationRaw") or "")),
                        url=None if url and is_linkedin_url(url) else url,
                        linkedin_url=canonical_linkedin(url or ""),
                    )
                )
        except Exception:  # noqa: BLE001 — a model that can't read this email costs this email
            logfire.warn("The fallback couldn't read an alert from {domain}", domain=domain)
            return [], "model_failed"

    usable = [c for c in cards if c.title and c.company]
    return [(meta, card) for card in usable], None if usable else "no_cards"


def search_criteria(last_uid: int | None, now: datetime) -> list[Any]:
    """MAIL-09: the last 14 days on a first connect, then only what is newer than the last UID.

    The second form is what keeps a check every three hours cheap — and idempotent, because a
    message already processed is never fetched again (SCH-02).
    """
    if last_uid is None:
        return ["SINCE", (now - timedelta(days=FIRST_RUN_DAYS)).date()]
    return ["UID", f"{last_uid + 1}:*"]


# ---------------------------------------------------------------------------
# The mailbox itself (MAIL-01, MAIL-02)


@contextmanager
def open_folder(host: str, user: str, password: str, folder: str) -> Iterator[Any]:
    """The alerts folder, read-only.

    `readonly=True` is IMAP's `EXAMINE`, which cannot set `\\Seen` — so a check never changes
    what the person sees as unread (MAIL-02, T-08). Nothing here writes, moves or deletes.
    """
    from imapclient import IMAPClient

    client = IMAPClient(host, ssl=True, timeout=30)
    try:
        client.login(user, password)
        client.select_folder(folder, readonly=True)
        yield client
    finally:
        with suppress(Exception):
            client.logout()


def check(
    *,
    host: str,
    user: str,
    password: str,
    folder: str,
    senders: list[Sender],
    last_uid: int | None,
    now: datetime,
    extract: Any = None,
    limit: int = 200,
) -> InboxResult:
    """One pass over the alerts folder. Returns cards and counts; stores no bodies (MAIL-08)."""
    result = InboxResult(last_uid=last_uid)
    try:
        with open_folder(host, user, password, folder) as client:
            uids = client.search(search_criteria(last_uid, now))[:limit]
            if not uids:
                return result
            # RFC822.SIZE first, so an oversized message is skipped without downloading it.
            sizes = client.fetch(uids, ["RFC822.SIZE"])
            for uid in sorted(uids):
                size = (sizes.get(uid) or {}).get(b"RFC822.SIZE", 0)
                result.checked += 1
                result.last_uid = max(result.last_uid or 0, int(uid))
                if size and size > MAX_BYTES:  # MAIL-10
                    result.skipped_too_large += 1
                    logfire.info("Skipped an alert of {size} bytes", size=size)
                    continue
                # BODY.PEEK[] — the whole message without setting \\Seen.
                fetched = client.fetch([uid], ["BODY.PEEK[]"])
                raw = (fetched.get(uid) or {}).get(b"BODY[]")
                if not raw:
                    continue
                cards, reason = read_message(raw, int(uid), senders, extract=extract)
                if reason == "rejected_unauthenticated":
                    result.rejected_unauthenticated += 1
                elif reason == "too_large":
                    result.skipped_too_large += 1
                result.cards.extend(cards)
    except Exception as exc:  # noqa: BLE001 — a mailbox that won't open costs this channel only
        logfire.warn("The alert inbox check failed: {error}", error=type(exc).__name__)
        result.errors.append(f"inbox: {type(exc).__name__}")
    return result
