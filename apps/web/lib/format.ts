import type { Ats, Channel, Job, Role, ScoredJob } from "./types";

const DAY_MS = 24 * 60 * 60 * 1000;

const CURRENCY: Record<string, string> = { GBP: "£", EUR: "€", USD: "$", CHF: "CHF " };

export function formatSalary(salary: Job["salary"]): string {
  if (!salary || (salary.min == null && salary.max == null)) return "salary not listed";
  const sym = CURRENCY[salary.currency] ?? `${salary.currency} `;
  const k = (n: number) => `${Math.round(n / 1000)}k`;
  if (salary.min != null && salary.max != null) return `${sym}${Math.round(salary.min / 1000)}–${k(salary.max)}`;
  return `${sym}${k((salary.min ?? salary.max)!)}`;
}

export function formatAge(iso: string | undefined, now: Date = new Date()): string {
  if (!iso) return "date unknown";
  const days = Math.round((now.getTime() - new Date(iso).getTime()) / DAY_MS);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  const weeks = Math.round(days / 7);
  return weeks === 1 ? "1 week ago" : `${weeks} weeks ago`;
}

/**
 * The age, hedged when the date was a hedge.
 *
 * Only an employer's own ATS feed gives a real timestamp. A board or an alert email says
 * "3 days ago", which `normalise.parse_posted` turns into an ISO string indistinguishable
 * from a real one — so the hedge has to be carried through from `postedPrecision` or the
 * screen quietly overstates what WERA knows.
 */
export function formatPostedAge(job: Pick<Job, "postedAt" | "postedPrecision">, now: Date = new Date()): string {
  // SRCH-24: an undated posting is kept, and says so in the person's words rather than WERA's.
  if (!job.postedAt) return "Date not given";
  const age = formatAge(job.postedAt, now);
  if (job.postedPrecision !== "approximate") return age;
  return age === "today" ? "posted today (approx.)" : `about ${age}`;
}

/**
 * MATCH-10: what WERA knows about whether this posting is still open.
 *
 * Returns empty for `open` — a confirmed job needs no annotation, and labelling every live job
 * would make the one unconfirmed job invisible. `unverified` is deliberately distinct from
 * `unreachable`: WERA never asked versus WERA asked and couldn't tell, and claiming to have
 * checked a job it never looked at would be a small lie told on every card.
 */
export function formatLiveness(
  job: Pick<ScoredJob, "liveness" | "lastConfirmedAt" | "closedAt"> & Partial<Pick<ScoredJob, "possiblyClosed">>,
  now: Date = new Date(),
): string {
  // LIFE-03: on the "Possibly closed" list, say why it is there before anything else.
  if (job.possiblyClosed && job.liveness !== "gone") return "not confirmed open in over a week";
  if (job.liveness === "gone") {
    const when = formatAge(job.closedAt, now);
    return when === "date unknown" ? "no longer listed" : `no longer listed · closed ${when}`;
  }
  if (job.liveness === "unreachable") {
    const when = formatAge(job.lastConfirmedAt, now);
    return when === "date unknown" ? "couldn't confirm this is still open" : `last confirmed open ${when}`;
  }
  return "";
}

/** SRCH-25: new from the moment a run adds it until the person opens it or decides on it. Jobs
 *  saved before tagging existed have no `foundBy`, and none of them is new to anyone. */
export function isNew(job: Pick<Job, "foundBy" | "seenAt">): boolean {
  return Boolean(job.foundBy) && !job.seenAt;
}

/** SRCH-25, 22 Nov: whether a job belongs in the Jobs screen's New view. The server's answer, not
 *  this session's opens: a card opened here loses its "New" tag at once but stays in the view until
 *  the next load, so opening it never makes it vanish. */
export const inNewView = (job: Pick<Job, "foundBy" | "seenAt">): boolean => isNew(job);

const ORIGIN: Record<Channel, string> = {
  board_search: "Search",
  email_alert: "Email",
  watchlist: "Employer feed",
  manual: "Added",
};

/** SRCH-31/32: the job APIs, as a job's origin names them ("Search 12 Nov, 08:54 · via Reed"). */
export const VIA: Record<string, string> = {
  reed: "Reed",
  usajobs: "USAJOBS",
  adzuna: "Adzuna",
  careerjet: "Careerjet",
  reliefweb: "ReliefWeb",
  himalayas: "Himalayas",
  // 22 Nov: Arbeitnow and The Muse (full text), Jooble (snippets behind a link never fetched).
  arbeitnow: "Arbeitnow",
  themuse: "The Muse",
  jooble: "Jooble",
};

/** The label a job API's terms ask for on every job it found, linked: Adzuna's to its home page
 *  (SRCH-32), Himalayas' to the job's own page there, or its home page without one (22 Nov). The
 *  job's Apply is untouched by this — for Himalayas it is the `applicationLink`, as their terms
 *  require. Null for a job no such API found. */
export function attribution(job: Pick<Job, "via" | "viaUrl">): { label: string; href: string } | null {
  if (job.via === "adzuna") return { label: "Jobs by Adzuna", href: "https://www.adzuna.co.uk" };
  if (job.via === "himalayas") return { label: "Jobs by Himalayas", href: job.viaUrl || "https://himalayas.app" };
  return null;
}

/** Which run found a job, to the minute: "Search 17 Nov, 08:54". The minute, not just the day,
 *  because two searches on one day are two different searches (17 Nov). */
export function foundLabel(found: NonNullable<Job["foundBy"]>): string {
  return `${ORIGIN[found.channel] ?? "Found"} ${formatDate(found.at)}, ${formatTime(found.at)}`;
}

/** Where a job came from and when: "Search 17 Nov, 08:54 · via Reed". Empty for a job saved
 *  before tagging. */
export function formatOrigin(job: Pick<Job, "foundBy" | "via">): string {
  if (!job.foundBy) return "";
  const via = job.via ? ` · via ${VIA[job.via] ?? job.via}` : "";
  return `${foundLabel(job.foundBy)}${via}`;
}

/** The urgency groups, in the order the Jobs screen shows them. Mirrors `domain.scoring.URGENCY`. */
export const URGENCY_GROUPS = [
  { id: "today", label: "Today and yesterday", hint: "Apply to these first — a graduate posting can close inside a week." },
  { id: "this_week", label: "This week", hint: "Posted in the last 7 days." },
  { id: "two_weeks", label: "Last two weeks", hint: "Still open, but the pile of applicants is deeper." },
  { id: "older", label: "Over two weeks old", hint: "Worth checking the posting is still live before you spend time on it." },
  // Deliberately its own group rather than folded into "older": filing a job under "over two
  // weeks old" claims WERA knows when it was posted, and for these it doesn't.
  { id: "unknown", label: "No date given", hint: "The source didn't say when this was posted, so it could be either." },
] as const;

export function formatAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const mins = Math.round((now.getTime() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  return formatAge(iso, now);
}

export function formatDate(iso: string | undefined, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-GB", opts).format(new Date(iso));
}

export function formatTime(iso: string | undefined): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

export const year = (ym?: string) => ym?.slice(0, 4) ?? "";

/** TAIL-02: role dates come from the master CV, never from model output. Shared by the review paper and the exports. */
export function roleDates(role: Pick<Role, "start" | "end">): string {
  const end = role.end === "present" ? "present" : year(role.end);
  return year(role.start) === end ? end : `${year(role.start)} – ${end}`;
}

export function formatLocation(job: Pick<Job, "location" | "remote">): string {
  const place = job.location.city ?? job.location.raw;
  return job.remote && job.remote !== "onsite" ? `${place}, ${job.remote}` : place;
}

const SITE_NAMES: Record<string, string> = {
  "brightnetwork.co.uk": "Bright Network",
  "gradcracker.com": "Gradcracker",
  "welcometothejungle.com": "Welcome to the Jungle",
  "builtinlondon.uk": "Built In London",
};

const ALERT_NAMES: Record<string, string> = {
  "linkedin-alert": "LinkedIn alert",
  "indeed-alert": "Indeed alert",
  "brightnetwork-alert": "Bright Network alert",
  "gradcracker-alert": "Gradcracker alert",
};

// The employer job systems a job can carry. The last four are international organisations' (SRCH-34).
export const VENDOR_NAMES: Record<Ats["vendor"], string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  un_careers: "UN Careers",
  taleo: "Taleo",
  workday: "Workday",
  successfactors: "SuccessFactors",
};

export function siteName(domain: string): string {
  return SITE_NAMES[domain] ?? domain;
}

export function sourceName(channel: Channel, detail: string): string {
  switch (channel) {
    case "board_search":
      return `${siteName(detail)} search`;
    case "email_alert":
      return ALERT_NAMES[detail] ?? "Alert email";
    case "watchlist":
      return "Watchlist";
    case "manual":
      return "Pasted by you";
  }
}

/** "LinkedIn alert → full post on Greenhouse", "Bright Network search + Indeed alert · Lever", … */
export function sourceLine(job: Pick<Job, "sources" | "ats" | "descriptionStatus">): string {
  const names = [...new Set(job.sources.map((s) => sourceName(s.channel, s.detail)))];
  const joined = names.join(" + ");
  if (job.descriptionStatus === "missing") return `${joined} only · no public posting found`;
  if (!job.ats) return joined;
  const vendor = VENDOR_NAMES[job.ats.vendor];
  const onlyAlerts = job.sources.every((s) => s.channel === "email_alert");
  return onlyAlerts ? `${joined} → full post on ${vendor}` : `${joined} · ${vendor}`;
}

export function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

export function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export function plural(n: number, word: string, pluralWord = `${word}s`): string {
  return `${n} ${n === 1 ? word : pluralWord}`;
}

/** A skill's place on the Upskills page, so a pack's "Missing" list links straight to it (UPSK-04). */
export function upskillAnchor(term: string): string {
  return `skill-${term.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}`;
}

/** LIFE-02: when applications close, when a source said. Empty without a date, or once it has passed
 *  (the job is closed by then). */
export function formatClosing(job: Pick<ScoredJob, "closesAt">, now: Date = new Date()): string {
  if (!job.closesAt) return "";
  const days = Math.round((Date.parse(`${job.closesAt.slice(0, 10)}T23:59:59`) - now.getTime()) / 86_400_000);
  if (days < 0) return "";
  if (days === 0) return "closes today";
  if (days === 1) return "closes tomorrow";
  return days <= 14 ? `closes in ${days} days` : `closes ${formatDate(job.closesAt)}`;
}

/** SRCH-34: an international organisation's grade, and who the post is open to. */
export function formatInternational(job: Pick<ScoredJob, "international">): string {
  const post = job.international;
  if (!post) return "";
  const who = post.recruitment === "international" ? "internationally recruited" : post.recruitment === "local" ? "locally recruited" : "";
  return [post.grade, who].filter(Boolean).join(" · ");
}

/** LIFE-04: open two months or more — often a standing advert rather than a vacancy. */
export function formatEvergreen(job: Pick<ScoredJob, "evergreen">): string {
  return job.evergreen ? "open 60+ days — may be a standing advert" : "";
}

const SMALL_WORDS = new Set(["of", "and", "the", "for", "de", "du", "la", "le", "von", "van", "der", "da", "di", "y", "&"]);

/** A name written all in capitals, in ordinary case (20 Nov): "ELEANOR VANCE" → "Eleanor Vance".
 *  The agent's `text.display_case`, so a sign-off reads the same on the page and in the PDF. Only
 *  text that is *all* capitals changes; capital words of two letters, or with no vowel, are
 *  acronyms and stay. With `brand`, a single capitalised word stays too ("AECOM", "KPMG"). */
export function displayCase(s: string, { brand = false }: { brand?: boolean } = {}): string {
  const text = s.split(/\s+/).filter(Boolean).join(" ");
  if (!/\p{L}/u.test(text) || text !== text.toUpperCase()) return text;
  const words = text.split(" ");
  if (brand && words.length === 1) return text;
  return words
    .map((word, i) => {
      const bare = word.replace(/[^A-Za-z]/g, "");
      if (i > 0 && SMALL_WORDS.has(word.toLowerCase())) return word.toLowerCase();
      if (words.length > 1 && bare && (bare.length <= 2 || (bare.length <= 5 && !/[AEIOU]/.test(bare)))) return word;
      return word
        .toLowerCase()
        .replace(/(^|[^\p{L}])(\p{L})/gu, (_m, pre: string, ch: string) => pre + ch.toUpperCase())
        .replace(/(?<=['’])S\b/g, "s");
    })
    .join(" ");
}

/** 21 Nov: a preview's label, from its CV match (`previewMatch`) and the agent's cut-offs. Never
 *  the number: the match is a similarity, not a fit, and "0.81" beside a fit of 8.1 would read as
 *  one scale. Without cut-offs (an older agent) these are the agent's own defaults. */
export type PreviewLabel = { id: "strong" | "good" | "possible"; label: string };

export const PREVIEW_CUTOFFS = { strong: 0.8, good: 0.72 } as const;

export function previewLabel(match: number | null | undefined, cutoffs: { strong: number; good: number } = PREVIEW_CUTOFFS): PreviewLabel {
  if (match != null && match >= cutoffs.strong) return { id: "strong", label: "Strong match" };
  if (match != null && match >= cutoffs.good) return { id: "good", label: "Good match" };
  return { id: "possible", label: "Possible match" };
}

/** A preview's employer line: the employer, and the job API that found it ("via Adzuna"). An
 *  agency's repost names the agency too, as a scored card does. A link the hunter found (22 Nov,
 *  `foundOn`) names the site it was found on, and only an employer something named: "Found on
 *  Totaljobs" when nothing did, never the board as if it were hiring. */
export function previewSource(job: Pick<Job, "company" | "postedBy" | "via" | "foundOn"> & { employerNamed?: boolean }): string {
  if (job.foundOn) {
    const site = job.foundOn.site;
    return job.foundOn.employerKnown && job.employerNamed !== false ? `${employerLine(job, "posted by")} · found on ${site}` : `Found on ${site}`;
  }
  const via = job.via ? ` · via ${VIA[job.via] ?? job.via}` : "";
  return `${employerLine(job, "posted by")}${via}`;
}

/** Previews best match first, then newest posted — the order the agent sends, kept after a local
 *  change (one read or dismissed) so the list never reshuffles under the pointer. */
export function sortPreviews<T extends Pick<Job, "previewMatch" | "postedAt">>(jobs: T[]): T[] {
  return [...jobs].sort(
    (a, b) => (b.previewMatch ?? -1) - (a.previewMatch ?? -1) || (b.postedAt ?? "").localeCompare(a.postedAt ?? ""),
  );
}

/** How many previews the section's heading counts: the agent's total less the ones dismissed or
 *  read here that this page's list still carries. A page refreshed since has already dropped them
 *  from `total`, so ids no longer in the list aren't taken off twice. */
export function previewsLeft(total: number, listed: Pick<Job, "id">[], gone: ReadonlySet<string>): number {
  const pending = listed.filter((j) => gone.has(j.id)).length;
  return Math.max(total - pending, listed.length - pending, 0);
}

/** H4 (21 Nov): what is left of the two allowances a search spends, as the Settings line shows
 *  them. A limit the agent doesn't report is left out rather than guessed. */
export function allowanceParts(s: {
  googleSearchesLeftToday?: number | null;
  googleSearchesPerDay?: number | null;
  weeklyCreditCap?: number | null;
  creditsThisWeek?: number | null;
}): string[] {
  const parts: string[] = [];
  if (s.googleSearchesLeftToday != null && s.googleSearchesPerDay != null) {
    parts.push(`Google searches left today: ${Math.max(s.googleSearchesLeftToday, 0)} of ${s.googleSearchesPerDay}`);
  }
  if (s.weeklyCreditCap != null) {
    parts.push(`Firecrawl credits left this week: ${Math.max(s.weeklyCreditCap - (s.creditsThisWeek ?? 0), 0)} of ${s.weeklyCreditCap}`);
  }
  return parts;
}

/** 22 Nov: who a job is for, as its card and review screen say it. An agency's repost reads under
 *  the employer it names, with the agency beside it ("Citi (via eFinancialCareers)", 20 Nov); a
 *  board's or agency's post that names no employer says so rather than show the board as the
 *  employer (Reed gives "eFinancialCareers" as the employer of the posts it carries for that
 *  board). The agent decides which is which (`employerNamed`). */
export function employerLine(job: { company: string; postedBy?: string | null; employerNamed?: boolean }, by: "via" | "posted by" = "via"): string {
  if (job.employerNamed === false) return `Employer not named (${by} ${job.company})`;
  return job.postedBy ? `${job.company} (${by} ${job.postedBy})` : job.company;
}
