"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiRequestError } from "@/lib/api";
import type { ChannelSummary, PlaceOption, ProfileProposalResult, Recency, SearchProfile, Seniority, SiteOption } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card, SectionLabel } from "@/lib/ui/Card";
import { Chip, ChipList } from "@/lib/ui/Chip";
import { ChipInput } from "@/lib/ui/ChipInput";
import { boardsFor, countryName, servesCountries } from "@/lib/regions";
import { cn } from "@/lib/ui/cn";
import { Dialog } from "@/lib/ui/Dialog";
import { Checkbox, FieldError, Label, Select } from "@/lib/ui/Field";
import { useToast } from "@/lib/ui/Toast";
import dynamic from "next/dynamic";
import { savedMessage, WhereFields, whereSummary } from "./WhereFields";

const RegenerateDialog = dynamic(() => import("./RegenerateDialog").then((m) => m.RegenerateDialog));

// `SiteOption` comes from the generated contracts (SCH-03). It used to be re-declared here by
// hand, which is why the UI couldn't see `countries` or `region` the moment the agent grew them
// — a hand-written copy of a generated type drifts silently and typechecks the whole way.
export type { SiteOption };

const CHANNEL_TITLES = { board_search: "Board search", email_alert: "Alert inbox", watchlist: "ATS watchlist" } as const;

const RECENCY: Record<Recency, string> = { "24h": "the last 24 hours", "7d": "the last 7 days", "21d": "the last 3 weeks" };

export function ProfileCard({
  profile,
  channels,
  sites,
  places,
  cvVersion,
  onPasteJob,
}: {
  profile: SearchProfile;
  channels: ChannelSummary[];
  sites: SiteOption[];
  places: PlaceOption[];
  cvVersion: number;
  onPasteJob: () => void;
}) {
  const toast = useToast();
  // Two ways in: the whole profile, or just where. Location is the setting people change most
  // and it sat ninth of eleven sections in the full dialog, below the boards' terms of service.
  const [editing, setEditing] = useState<"all" | "where">();
  const [proposal, setProposal] = useState<ProfileProposalResult>();
  const [generating, setGenerating] = useState(false);
  // PRF-04: the CV has moved on since this profile was generated.
  const stale = cvVersion > profile.generatedFromCvVersion;

  const regenerate = async () => {
    setGenerating(true);
    try {
      setProposal(await api.generateProfile());
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't propose a profile." });
    } finally {
      setGenerating(false);
    }
  };

  const [open, setOpen] = useState(false);
  const more = (items: string[], n: number) => (items.length > n ? `${items.slice(0, n).join(", ")} +${items.length - n}` : items.join(", "));
  const levels = SENIORITY.filter((s) => profile.seniority.includes(s.id)).map((s) => s.label);

  // What WERA searches for, where, and at what level — each one a button into the setting it
  // names, so changing the location is one click from the shortlist rather than a hunt.
  return (
    <Card className="px-5 py-4 sm:px-6" aria-labelledby="profile-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="profile-title" className="text-small font-semibold tracking-wide text-ink-2 uppercase">
          Search profile
        </h2>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" className="min-h-9 px-3.5 text-[14px]" onClick={regenerate} busy={generating}>
            {generating ? "Reading your CV…" : "Regenerate from CV"}
          </Button>
          <Button variant="secondary" className="min-h-9 px-3.5 text-[14px]" onClick={() => setEditing("all")}>
            Edit profile
          </Button>
        </div>
      </div>

      <div className="mt-3 grid gap-px overflow-hidden rounded-control border border-line bg-line @2xl:grid-cols-[1.3fr_1fr_1fr]">
        <Summary label="Looking for" value={more(profile.titles, 2) || "No target titles yet"} title={profile.titles.join(", ")} onClick={() => setEditing("all")} />
        <Summary label="Where" value={whereSummary(profile, places)} action="Change" onClick={() => setEditing("where")} prominent />
        <Summary
          label="Level"
          value={`${more(levels, 2) || "Any level"} · posted in ${RECENCY[profile.recency]}`}
          title={`${levels.join(", ") || "Any level"} · posted in ${RECENCY[profile.recency]}`}
          onClick={() => setEditing("all")}
        />
      </div>

      {stale ? (
        <p className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-3.5 py-2.5 text-small text-amber-700">
          This profile came from master CV v{profile.generatedFromCvVersion}; you&apos;re on v{cvVersion} now. Regenerate to see what
          would change — you choose what to keep.
        </p>
      ) : null}

      {open ? (
        <div id="profile-details" className="animate-fade-in">
          <div className="mt-4 grid gap-x-8 gap-y-4 border-t border-line pt-4 sm:grid-cols-2">
            <ChipGroup label="Target titles" items={profile.titles} />
            <ChipGroup label="Top skills" items={profile.skills} />
            <ChipGroup label="Excluding" items={profile.exclude} outline />
          </div>

          <ul className="mt-4 grid gap-px border border-line bg-line md:grid-cols-3">
            {channels.map((c) => (
              <li key={c.channel} className={cn("bg-surface p-4", !c.enabled && "opacity-70")}>
                <p className="flex items-center gap-2 text-[14px] font-bold">
                  <span className={cn("size-2 rounded-dot", c.enabled ? "bg-accent" : "bg-line")} aria-hidden="true" />
                  {CHANNEL_TITLES[c.channel]}
                  {!c.enabled ? <span className="text-small font-normal text-ink-2">· off</span> : null}
                </p>
                <p className="mt-1 text-small text-ink-2">{c.detail}</p>
                <p className="mt-1 text-small text-ink">{c.stats}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-x-4">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls="profile-details"
          className="min-h-8 text-small font-semibold text-ink-2 underline-offset-4 hover:text-ink hover:underline pointer-coarse:min-h-11"
        >
          {open ? "Hide skills and channels" : "Skills and channels"}
        </button>
        <button
          type="button"
          onClick={onPasteJob}
          className="min-h-8 text-small font-semibold text-forest-700 underline-offset-4 hover:underline pointer-coarse:min-h-11"
        >
          Already have a link? Paste a job instead
        </button>
      </div>

      {editing === "all" ? <ProfileDialog profile={profile} sites={sites} places={places} onClose={() => setEditing(undefined)} /> : null}
      {editing === "where" ? <WhereDialog profile={profile} sites={sites} places={places} onClose={() => setEditing(undefined)} /> : null}
      {proposal ? <RegenerateDialog result={proposal} current={profile} onClose={() => setProposal(undefined)} /> : null}
    </Card>
  );
}

function Summary({
  label,
  value,
  title,
  action = "Edit",
  prominent,
  onClick,
}: {
  label: string;
  value: string;
  title?: string;
  action?: string;
  /** The action in the accent colour: this is the one people look for. */
  prominent?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex min-w-0 flex-col items-start gap-0.5 bg-surface px-3.5 py-3 text-left transition-colors hover:bg-mint-50 focus-visible:relative focus-visible:z-10"
    >
      <span className="flex w-full items-baseline justify-between gap-2 text-small text-ink-2">
        {label}
        {/* Always shown, quieter where it isn't the one people look for. It used to appear only on
            hover, and a touch screen never hovers — nothing said these tiles could be tapped. */}
        <span className={cn("font-semibold", prominent ? "text-forest-700" : "text-ink-2 group-hover:text-forest-700")}>{action}</span>
      </span>
      {/* Two lines, then cut: a single truncated line hid most of "Mid level, Senior +1 · posted
          in the last 7 days" on a phone, and the tooltip holding the rest doesn't open on touch. */}
      <span className="line-clamp-2 w-full text-[15px] font-semibold text-ink [overflow-wrap:anywhere]" title={title ?? value}>
        {value}
      </span>
    </button>
  );
}

function ChipGroup({ label, items, outline }: { label: string; items: string[]; outline?: boolean }) {
  return (
    <div>
      <SectionLabel className="mb-1.5">{label}</SectionLabel>
      <ChipList>
        {items.length ? (
          items.map((t) => (
            <Chip key={t} variant={outline ? "outline" : "mint"}>
              {t}
            </Chip>
          ))
        ) : (
          <span className="text-small text-ink-2">None</span>
        )}
      </ChipList>
    </div>
  );
}

/** Saving a profile, from either dialog: the toast, the refresh, and the field errors. */
function useSaveProfile(onClose: () => void) {
  const router = useRouter();
  const toast = useToast();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const save = async (profile: SearchProfile) => {
    setSaving(true);
    setErrors({});
    try {
      const saved = await api.putProfile(profile);
      toast({ tone: "ok", message: savedMessage(saved) });
      router.refresh();
      onClose();
    } catch (err) {
      if (err instanceof ApiRequestError && err.fields) setErrors(err.fields);
      else toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't save the profile." });
    } finally {
      setSaving(false);
    }
  };
  return { save, saving, errors };
}

/** SRCH-11: just where — countries, cities and remote — without the other ten settings. */
function WhereDialog({ profile, sites, places, onClose }: { profile: SearchProfile; sites: SiteOption[]; places: PlaceOption[]; onClose: () => void }) {
  const [draft, setDraft] = useState(profile);
  const { save, saving, errors } = useSaveProfile(onClose);
  const boards = boardsFor(sites, draft.countries, draft.sites);

  return (
    <Dialog
      open
      onClose={onClose}
      title="Where to search"
      description="Every search after this one looks here, and the jobs already found follow it: ones elsewhere are hidden, hidden ones here come back."
      footer={
        <>
          <span className="mr-auto self-center text-small text-ink-2">
            Web search{boards.length ? ` and ${boards.length} of your boards` : ""} cover{boards.length ? "" : "s"} this
          </span>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save(draft)} busy={saving}>
            Save location
          </Button>
        </>
      }
    >
      <WhereFields value={draft} onChange={(next) => setDraft((d) => ({ ...d, ...next }))} places={places} errors={errors} />
    </Dialog>
  );
}

const SENIORITY: { id: Seniority; label: string }[] = [
  { id: "intern", label: "Intern" },
  { id: "graduate", label: "Graduate" },
  { id: "junior", label: "Junior" },
  { id: "entry", label: "Entry level" },
  { id: "mid", label: "Mid level" },
  // The three above `mid` are what make this setting do anything: the filter hides titles
  // carrying "Senior", "Lead" or "Principal", and until these existed there was no way to say
  // you'd take one.
  { id: "senior", label: "Senior" },
  { id: "lead", label: "Lead / Staff" },
  { id: "principal", label: "Principal" },
];

/**
 * SRCH-12: boards WERA found and is asking about. SRCH-04 is why this exists rather than
 * discovery just enabling them.
 *
 * The button says "I've reviewed the terms" and not "Enable", and that wording is the feature.
 * `terms_reviewed_on` records that a person read a third party's terms of service and robots.txt
 * (SPEC §11.3). A button labelled "Enable" would reduce that to someone clicking past a dialog —
 * so the real postings and the links to the site and its robots.txt are put in front of them
 * first, and the assertion is spelled out.
 */
function ProposedBoards({ sites }: { sites: SiteOption[] }) {
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState<string>();
  const proposed = sites.filter((s) => s.proposed);

  if (!proposed.length) return null;

  const decide = async (domain: string, action: "approve" | "decline") => {
    setBusy(domain);
    try {
      await api.reviewSite(domain, action);
      toast({
        tone: "ok",
        message: action === "approve" ? `${domain} will be searched from your next search.` : `${domain} won't be suggested again.`,
      });
      router.refresh();
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't save that." });
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <fieldset className="sm:col-span-2">
      <legend className="mb-1.5 text-[14px] font-semibold">
        Boards WERA found
        <span className="ml-2 font-normal text-ink-2">{proposed.length} waiting</span>
      </legend>
      <p className="mb-3 text-small text-ink-2">
        These came up while searching for your titles. WERA won&apos;t search any of them until you&apos;ve read their terms — that
        decision is yours, not WERA&apos;s.
      </p>
      <ul className="grid gap-3">
        {proposed.map((s) => (
          <li key={s.domain} className="rounded-none border border-line p-3.5">
            <p className="text-[14px] font-bold">{s.domain}</p>
            <p className="mt-0.5 text-small text-ink-2">
              Found searching for {s.matched ?? "your titles"}
              {s.robotsOk === false ? " · robots.txt disallows crawling" : s.robotsOk === null ? " · robots.txt couldn't be read" : ""}
            </p>
            {s.examples.length ? (
              <ul className="mt-2 grid gap-0.5">
                {s.examples.map((url) => (
                  <li key={url}>
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-small text-forest-700 underline underline-offset-2 [overflow-wrap:anywhere] hover:text-forest-900"
                    >
                      {url.replace(/^https?:\/\//, "").slice(0, 70)}
                    </a>
                  </li>
                ))}
              </ul>
            ) : null}
            <p className="mt-2 flex flex-wrap gap-x-3 text-small">
              <a
                href={`https://${s.domain}/robots.txt`}
                target="_blank"
                rel="noreferrer noopener"
                className="text-ink-2 underline underline-offset-2 hover:text-ink"
              >
                robots.txt
              </a>
              <a
                href={`https://${s.domain}`}
                target="_blank"
                rel="noreferrer noopener"
                className="text-ink-2 underline underline-offset-2 hover:text-ink"
              >
                Open the site
              </a>
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {/* Full width and allowed to wrap on a phone: on one line this label is wider than the
                  dialog there, and the dialog cut it off. */}
              <Button busy={busy === s.domain} onClick={() => decide(s.domain, "approve")} className="max-sm:w-full max-sm:whitespace-normal">
                I&apos;ve reviewed the terms — search it
              </Button>
              <Button variant="secondary" disabled={busy === s.domain} onClick={() => decide(s.domain, "decline")} className="max-sm:w-full">
                Not this one
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

function ProfileDialog({ profile, sites, places, onClose }: { profile: SearchProfile; sites: SiteOption[]; places: PlaceOption[]; onClose: () => void }) {
  const [draft, setDraft] = useState(profile);
  const { save, saving, errors } = useSaveProfile(onClose);
  const set = <K extends keyof SearchProfile>(key: K, value: SearchProfile[K]) => setDraft((d) => ({ ...d, [key]: value }));

  // SRCH-11: the grid is title × board, not title × location. Counted the same way the agent
  // counts it, so the footer and the search dialog can't promise different numbers.
  const boards = boardsFor(sites, draft.countries, draft.sites);
  const queries = draft.channels.boardSearch ? draft.titles.length * boards.length : 0;

  // Three sections in the order people think about a search: what, where, and where WERA looks.
  // It used to be one grid of eleven fields with the location ninth, under the boards' terms.
  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title="Edit search profile"
      description={`Generated from master CV v${profile.generatedFromCvVersion}. Results are always matched back against the CV.`}
      footer={
        <>
          <span className="mr-auto self-center text-small text-ink-2">
            {queries} board {queries === 1 ? "query" : "queries"} per search ({draft.titles.length} {draft.titles.length === 1 ? "title" : "titles"} ×{" "}
            {boards.length} {boards.length === 1 ? "board" : "boards"})
          </span>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save(draft)} busy={saving}>
            Save profile
          </Button>
        </>
      }
    >
      <Section title="What you're looking for">
        <div className="grid gap-5 sm:grid-cols-2">
          <ChipInput label="Target titles" value={draft.titles} onChange={(v) => set("titles", v)} max={8} error={errors.titles} placeholder="Add a title" />
          <ChipInput label="Top skills" value={draft.skills} onChange={(v) => set("skills", v)} max={15} error={errors.skills} placeholder="Add a skill" />
          <fieldset>
            <legend className="mb-1.5 text-[14px] font-semibold">Seniority</legend>
            <div className="grid grid-cols-2 gap-x-3">
              {SENIORITY.map((s) => (
                <Checkbox
                  key={s.id}
                  label={s.label}
                  checked={draft.seniority.includes(s.id)}
                  onChange={(e) => set("seniority", e.target.checked ? [...draft.seniority, s.id] : draft.seniority.filter((x) => x !== s.id))}
                />
              ))}
            </div>
          </fieldset>
          <div className="grid content-start gap-5">
            <ChipInput
              label="Excluding"
              value={draft.exclude}
              onChange={(v) => set("exclude", v)}
              variant="outline"
              hint="Jobs with these words in the title are hidden before any model call."
              placeholder="Add a term"
            />
            <div>
              <Label htmlFor="recency">Posted within</Label>
              <Select id="recency" value={draft.recency} onChange={(e) => set("recency", e.target.value as Recency)}>
                <option value="24h">The last 24 hours</option>
                <option value="7d">The last 7 days</option>
                <option value="21d">The last 3 weeks</option>
              </Select>
            </div>
          </div>
        </div>
      </Section>

      <Section title="Where">
        <WhereFields value={draft} onChange={(next) => setDraft((d) => ({ ...d, ...next }))} places={places} errors={errors} />
      </Section>

      <Section title="Where WERA looks">
        <div className="grid gap-5">
          <fieldset>
            <legend className="mb-1.5 text-[14px] font-semibold">Boards to search</legend>
            {/* Proposed boards are offered below, not here: a disabled checkbox is the right
                affordance for a board nobody has looked at, but it is the wrong one for a board
                WERA is actively asking about. Declined boards disappear entirely. */}
            <div className="grid gap-x-4 sm:grid-cols-2">
              {sites
                .filter((s) => !s.proposed && !s.declined)
                .map((s) => {
                  const servesChoice = servesCountries(s, draft.countries);
                  const where = s.countries.length ? s.countries.map(countryName).join(", ") : "Anywhere";
                  return (
                    <Checkbox
                      key={s.domain}
                      label={s.name}
                      description={
                        !s.reviewed
                          ? `${s.domain} · terms not reviewed yet, so it can't be enabled`
                          : servesChoice
                            ? `${s.domain} · ${where}`
                            : `${s.domain} · ${where} — skipped, none of your countries`
                      }
                      disabled={!s.reviewed}
                      checked={draft.sites.includes(s.domain)}
                      onChange={(e) => set("sites", e.target.checked ? [...draft.sites, s.domain] : draft.sites.filter((x) => x !== s.domain))}
                    />
                  );
                })}
            </div>
            <p className="mt-1 text-small text-ink-2">
              Each enabled board gets its own query, so no single board can crowd out the others. linkedin.com is always excluded.
            </p>
          </fieldset>

          <ProposedBoards sites={sites} />

          <fieldset>
            <legend className="mb-1.5 text-[14px] font-semibold">Channels</legend>
            <Checkbox
              label="Board search"
              description="Google searches (Serper) across the web and the boards above"
              checked={draft.channels.boardSearch}
              onChange={(e) => set("channels", { ...draft.channels, boardSearch: e.target.checked })}
            />
            <Checkbox
              label="Alert inbox"
              description="Read-only check of your alerts folder"
              checked={draft.channels.inbox}
              onChange={(e) => set("channels", { ...draft.channels, inbox: e.target.checked })}
            />
            <Checkbox
              label="ATS watchlist"
              description="Public Greenhouse, Lever and Ashby feeds"
              checked={draft.channels.watchlist}
              onChange={(e) => set("channels", { ...draft.channels, watchlist: e.target.checked })}
            />
            <FieldError>{errors.channels}</FieldError>
          </fieldset>
        </div>
      </Section>
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t border-line pt-5 pb-6 first:border-t-0 first:pt-0 last:pb-0">
      <h3 className="mb-4 text-[16px] font-bold tracking-[-0.01em]">{title}</h3>
      {children}
    </section>
  );
}
