import type { Channel, Job, Role, ScoredJob } from "./types";

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
  const age = formatAge(job.postedAt, now);
  if (!job.postedAt || job.postedPrecision !== "approximate") return age;
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
  job: Pick<ScoredJob, "liveness" | "lastConfirmedAt" | "closedAt">,
  now: Date = new Date(),
): string {
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

const VENDOR_NAMES = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby" } as const;

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
