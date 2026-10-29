"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiRequestError } from "@/lib/api";
import type { ChannelSummary, ProfileProposalResult, Recency, SearchProfile, Seniority, SiteOption } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card, CardHeader, SectionLabel } from "@/lib/ui/Card";
import { Chip, ChipList } from "@/lib/ui/Chip";
import { ChipInput } from "@/lib/ui/ChipInput";
import { boardsFor, countriesByRegion, countryName, regionName, servesCountries } from "@/lib/regions";
import { cn } from "@/lib/ui/cn";
import { Dialog } from "@/lib/ui/Dialog";
import { Checkbox, FieldError, Label, Select } from "@/lib/ui/Field";
import { useToast } from "@/lib/ui/Toast";
import { RegenerateDialog } from "./RegenerateDialog";

// `SiteOption` comes from the generated contracts (SCH-03). It used to be re-declared here by
// hand, which is why the UI couldn't see `countries` or `region` the moment the agent grew them
// — a hand-written copy of a generated type drifts silently and typechecks the whole way.
export type { SiteOption };

const CHANNEL_TITLES = { board_search: "Board search", email_alert: "Alert inbox", watchlist: "ATS watchlist" } as const;

export function ProfileCard({
  profile,
  channels,
  sites,
  cvVersion,
  onPasteJob,
}: {
  profile: SearchProfile;
  channels: ChannelSummary[];
  sites: SiteOption[];
  cvVersion: number;
  onPasteJob: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
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

  return (
    <Card className="p-5 sm:p-6" aria-labelledby="profile-title">
      <CardHeader
        id="profile-title"
        title="Searching with your master CV"
        description="This search profile was generated from your CV. Edit it any time; every result is matched back against the CV."
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={regenerate} busy={generating}>
              Regenerate from CV
            </Button>
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Edit profile
            </Button>
          </div>
        }
      />

      {stale ? (
        <p className="mt-4 rounded-control bg-amber-50 px-3.5 py-2.5 text-small text-amber-700">
          This profile came from master CV v{profile.generatedFromCvVersion}; you&apos;re on v{cvVersion} now. Regenerate to see what
          would change — you choose what to keep.
        </p>
      ) : null}

      <div className="mt-5 grid gap-x-8 gap-y-4 sm:grid-cols-2">
        <ChipGroup label="Target titles" items={profile.titles} />
        <ChipGroup label="Top skills" items={profile.skills} />
        <ChipGroup label="Locations" items={profile.locations} />
        <ChipGroup label="Excluding" items={profile.exclude} outline />
      </div>

      <ul className="mt-5 grid gap-3 md:grid-cols-3">
        {channels.map((c) => (
          <li key={c.channel} className={cn("rounded-[12px] border border-line p-3.5", !c.enabled && "bg-mint-50")}>
            <p className="flex items-center gap-2 text-[14px] font-bold">
              <span className={cn("size-2 rounded-full", c.enabled ? "bg-forest-700" : "bg-line")} aria-hidden="true" />
              {CHANNEL_TITLES[c.channel]}
              {!c.enabled ? <span className="text-small font-normal text-ink-2">· off</span> : null}
            </p>
            <p className="mt-1 text-small text-ink-2">{c.detail}</p>
            <p className="mt-1 text-small text-ink">{c.stats}</p>
          </li>
        ))}
      </ul>

      <button type="button" onClick={onPasteJob} className="mt-4 min-h-8 text-small font-semibold text-forest-700 underline underline-offset-2 hover:text-forest-900">
        Already have a link? Paste a job instead
      </button>

      {editing ? <ProfileDialog profile={profile} sites={sites} onClose={() => setEditing(false)} /> : null}
      {proposal ? <RegenerateDialog result={proposal} current={profile} onClose={() => setProposal(undefined)} /> : null}
    </Card>
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
          <li key={s.domain} className="rounded-[12px] border border-line p-3.5">
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
                      className="text-small text-forest-700 underline underline-offset-2 hover:text-forest-900"
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
              <Button busy={busy === s.domain} onClick={() => decide(s.domain, "approve")}>
                I&apos;ve reviewed the terms — search it
              </Button>
              <Button variant="secondary" disabled={busy === s.domain} onClick={() => decide(s.domain, "decline")}>
                Not this one
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}

function ProfileDialog({ profile, sites, onClose }: { profile: SearchProfile; sites: SiteOption[]; onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const [draft, setDraft] = useState(profile);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const set = <K extends keyof SearchProfile>(key: K, value: SearchProfile[K]) => setDraft((d) => ({ ...d, [key]: value }));

  const save = async () => {
    setSaving(true);
    setErrors({});
    try {
      await api.putProfile(draft);
      toast({ tone: "ok", message: "Search profile saved. The next search uses it." });
      router.refresh();
      onClose();
    } catch (err) {
      if (err instanceof ApiRequestError && err.fields) setErrors(err.fields);
      else toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't save the profile." });
    } finally {
      setSaving(false);
    }
  };

  // SRCH-11: the grid is title × board, not title × location. Counted the same way the agent
  // counts it, so the footer and the search dialog can't promise different numbers.
  const boards = boardsFor(sites, draft.countries, draft.sites);
  const queries = draft.channels.boardSearch ? draft.titles.length * boards.length : 0;
  const regions = countriesByRegion(sites);

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
          <Button onClick={save} busy={saving}>
            Save profile
          </Button>
        </>
      }
    >
      <div className="grid gap-5 sm:grid-cols-2">
        <ChipInput label="Target titles" value={draft.titles} onChange={(v) => set("titles", v)} max={8} error={errors.titles} placeholder="Add a title" />
        <ChipInput label="Top skills" value={draft.skills} onChange={(v) => set("skills", v)} max={15} error={errors.skills} placeholder="Add a skill" />
        <ChipInput
          label="Cities (optional)"
          value={draft.locations}
          onChange={(v) => set("locations", v)}
          max={8}
          error={errors.locations}
          hint="Leave empty to search the whole of each country you picked."
          placeholder="Add a city"
        />
        <ChipInput
          label="Excluding"
          value={draft.exclude}
          onChange={(v) => set("exclude", v)}
          variant="outline"
          hint="Jobs with these words in the title are hidden before any model call."
          placeholder="Add a term"
        />

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

        <div className="grid content-start gap-4">
          <div>
            <Label htmlFor="remote">Remote</Label>
            <Select id="remote" value={draft.remote} onChange={(e) => set("remote", e.target.value as SearchProfile["remote"])}>
              <option value="any">Remote, hybrid or on-site</option>
              <option value="remote_only">Remote only</option>
              <option value="no_remote">On-site or hybrid only</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="recency">Posted within</Label>
            <Select id="recency" value={draft.recency} onChange={(e) => set("recency", e.target.value as Recency)}>
              <option value="24h">The last 24 hours</option>
              <option value="7d">The last 7 days</option>
              <option value="30d">The last 30 days</option>
            </Select>
          </div>
        </div>

        <fieldset className="sm:col-span-2">
          <legend className="mb-1.5 text-[14px] font-semibold">Where you want to work</legend>
          <p className="mb-2 text-small text-ink-2">
            Most boards only carry jobs in one country, so this decides which of them are worth searching. Pick nothing to search every board you have
            enabled.
          </p>
          {regions.map(({ region, codes }) => {
            const all = codes.every((c) => draft.countries.includes(c));
            return (
              <div key={region} className="mb-3">
                {/* The region itself is selectable, not just a heading. Country checkboxes under a
                    label is not a way to "set your region" — you can reach the same state, but only
                    by knowing which countries the region contains, which is the thing the heading
                    was supposed to save you. */}
                <Checkbox
                  label={regionName(region)}
                  description={`All ${codes.length} ${codes.length === 1 ? "country" : "countries"} WERA has boards for`}
                  checked={all}
                  onChange={() =>
                    set("countries", all ? draft.countries.filter((c) => !codes.includes(c)) : [...new Set([...draft.countries, ...codes])])
                  }
                />
                <div className="mt-1 ml-6 grid grid-cols-2 gap-x-3">
                  {codes.map((code) => (
                    <Checkbox
                      key={code}
                      label={countryName(code)}
                      checked={draft.countries.includes(code)}
                      onChange={(e) => set("countries", e.target.checked ? [...draft.countries, code] : draft.countries.filter((c) => c !== code))}
                    />
                  ))}
                </div>
              </div>
            );
          })}
          {draft.countries.length ? (
            <p className="text-small text-ink-2">
              {boards.length} of {sites.filter((s) => s.reviewed).length} reviewed boards cover{" "}
              {draft.countries.length === 1 ? "that country" : "those countries"}.
            </p>
          ) : null}
          {errors.countries ? <p className="text-small text-berry-700">{errors.countries}</p> : null}
        </fieldset>

        <fieldset className="sm:col-span-2">
          <legend className="mb-1.5 text-[14px] font-semibold">Boards to search</legend>
          {/* Proposed boards are offered below, not here: a disabled checkbox is the right
              affordance for a board nobody has looked at, but it is the wrong one for a board
              WERA is actively asking about. Declined boards disappear entirely. */}
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
                  onChange={(e) =>
                    set("sites", e.target.checked ? [...draft.sites, s.domain] : draft.sites.filter((x) => x !== s.domain))
                  }
                />
              );
            })}
          <p className="mt-1 text-small text-ink-2">
            Each enabled board gets its own query, so no single board can crowd out the others. linkedin.com is always excluded.
          </p>
        </fieldset>

        <ProposedBoards sites={sites} />

        <fieldset>
          <legend className="mb-1.5 text-[14px] font-semibold">Channels</legend>
          <Checkbox
            label="Board search"
            description="Firecrawl search on the boards above"
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
    </Dialog>
  );
}
