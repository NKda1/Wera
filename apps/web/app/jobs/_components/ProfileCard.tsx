"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiRequestError } from "@/lib/api";
import type { ChannelSummary, Recency, SearchProfile, Seniority } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card, CardHeader, SectionLabel } from "@/lib/ui/Card";
import { Chip, ChipList } from "@/lib/ui/Chip";
import { ChipInput } from "@/lib/ui/ChipInput";
import { cn } from "@/lib/ui/cn";
import { Dialog } from "@/lib/ui/Dialog";
import { Checkbox, FieldError, Label, Select } from "@/lib/ui/Field";
import { useToast } from "@/lib/ui/Toast";

export type SiteOption = { domain: string; name: string; reviewed: boolean };

const CHANNEL_TITLES = { board_search: "Board search", email_alert: "Alert inbox", watchlist: "ATS watchlist" } as const;

export function ProfileCard({
  profile,
  channels,
  sites,
  onPasteJob,
}: {
  profile: SearchProfile;
  channels: ChannelSummary[];
  sites: SiteOption[];
  onPasteJob: () => void;
}) {
  const [editing, setEditing] = useState(false);
  return (
    <Card className="p-5 sm:p-6" aria-labelledby="profile-title">
      <CardHeader
        id="profile-title"
        title="Searching with your master CV"
        description="This search profile was generated from your CV. Edit it any time; every result is matched back against the CV."
        action={
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Edit profile
          </Button>
        }
      />

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
];

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

  const queries = draft.channels.boardSearch ? draft.titles.length * draft.locations.length : 0;

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
            {queries} board {queries === 1 ? "query" : "queries"} per search ({draft.titles.length} titles × {draft.locations.length} locations)
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
        <ChipInput label="Locations" value={draft.locations} onChange={(v) => set("locations", v)} max={8} error={errors.locations} placeholder="Add a city" />
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

        <fieldset>
          <legend className="mb-1.5 text-[14px] font-semibold">Boards to search</legend>
          {sites.map((s) => (
            <Checkbox
              key={s.domain}
              label={s.name}
              description={s.reviewed ? s.domain : `${s.domain} · terms not reviewed yet, so it can't be enabled`}
              disabled={!s.reviewed}
              checked={draft.sites.includes(s.domain)}
              onChange={(e) => set("sites", e.target.checked ? [...draft.sites, s.domain] : draft.sites.filter((x) => x !== s.domain))}
            />
          ))}
          <p className="mt-1 text-small text-ink-2">linkedin.com is always excluded.</p>
        </fieldset>

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
