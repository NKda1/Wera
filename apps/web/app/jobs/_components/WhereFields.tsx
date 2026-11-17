"use client";

// SRCH-11: where a search looks. Shared by the quick "Where" dialog on the profile card and the
// full profile dialog, so the two can't drift into different ideas of what a location is.
//
// Countries come from the agent's `/places`, not from the boards. The picker used to offer only
// the countries a reviewed board covered — the UK and a handful of its neighbours — so anyone
// looking further afield had no way to say so, although the open web search and the job APIs
// reach every country listed here.

import { X } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { regionName } from "@/lib/regions";
import type { PlaceOption, SavedProfile, SearchProfile } from "@/lib/types";
import { ChipInput } from "@/lib/ui/ChipInput";
import { cn } from "@/lib/ui/cn";
import { Checkbox, Input, Label, Select } from "@/lib/ui/Field";

type Where = Pick<SearchProfile, "countries" | "locations" | "remote" | "cityScope" | "international">;

/** What searches a country, in a few words: the thing that decides whether picking it is useful. */
export function coverage(place: PlaceOption): string {
  const parts = ["Web search"];
  if (place.boards.length) parts.push(`${place.boards.length} ${place.boards.length === 1 ? "board" : "boards"}`);
  parts.push(...place.feeds);
  return parts.join(" · ");
}

/** "London first · United Kingdom, Switzerland", or "Anywhere" — the profile card's one-line answer. */
export function whereSummary(profile: Where, places: PlaceOption[]): string {
  const names = profile.countries.map((c) => places.find((p) => p.code === c)?.name ?? c);
  const cities = profile.locations.map((l) => l.split(",")[0].trim()).filter(Boolean);
  const remote = profile.remote === "remote_only" ? "Remote only" : "";
  const named = cities.length ? `${cities.join(", ")} ${profile.cityScope === "only" ? "only" : "first"}` : "";
  const place = [named, names.join(", ")].filter(Boolean).join(" · ");
  const orgs = profile.international ? "International organisations anywhere" : "";
  return [place || (remote ? "" : "Anywhere"), orgs, remote].filter(Boolean).join(" · ");
}

/** What a named city does to its country (16 Nov). Picked before each search, in the Search dialog. */
const SCOPES: { id: SearchProfile["cityScope"]; label: string; description: string }[] = [
  {
    id: "first",
    label: "These cities first, then the rest of the country",
    description: "Searches your cities first and keeps jobs anywhere else in those countries too.",
  },
  {
    id: "only",
    label: "Only these cities",
    description: "In a country where you named a city, only that city. Countries with no city are searched everywhere.",
  },
];

/** What a save says. When "where" changed, the jobs already found followed it, and it says how many. */
export function savedMessage(saved: SavedProfile): string {
  const moved = saved.refiltered;
  if (!moved) return "Search profile saved. The next search uses it.";
  const parts = [
    moved.shown ? `${moved.shown} ${moved.shown === 1 ? "job" : "jobs"} now shown` : "",
    moved.hidden ? `${moved.hidden} hidden` : "",
  ].filter(Boolean);
  return `Where saved${parts.length ? `: ${parts.join(", ")}` : ""}. The next search looks there.`;
}

export function WhereFields({
  value,
  onChange,
  places,
  errors = {},
}: {
  value: Where;
  onChange: (next: Partial<Where>) => void;
  places: PlaceOption[];
  errors?: Record<string, string>;
}) {
  const [query, setQuery] = useState("");
  const filterId = useId();
  const remoteId = useId();
  const chosen = value.countries;
  const byCode = useMemo(() => new Map(places.map((p) => [p.code, p])), [places]);

  const toggle = (code: string, on: boolean) => onChange({ countries: on ? [...chosen, code] : chosen.filter((c) => c !== code) });

  // Grouped by region, filtered by the box above. The chosen ones are always listed first, at the
  // top, so a long list never hides what is already picked.
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const shown = places.filter((p) => !q || p.name.toLowerCase().includes(q) || p.code.toLowerCase() === q);
    const regions = new Map<string, PlaceOption[]>();
    for (const p of shown) regions.set(p.region, [...(regions.get(p.region) ?? []), p]);
    return [...regions.entries()]
      .map(([region, list]) => ({ region, list: list.sort((a, b) => a.name.localeCompare(b.name)) }))
      .sort((a, b) => regionName(a.region).localeCompare(regionName(b.region)));
  }, [places, query]);

  return (
    <div className="grid gap-5">
      <fieldset>
        <legend className="mb-1.5 text-[14px] font-semibold">Countries</legend>
        {chosen.length ? (
          <ul className="mb-2.5 flex flex-wrap gap-1.5" aria-label="Countries chosen">
            {chosen.map((code) => (
              <li key={code} className="inline-flex items-center gap-1 bg-mint-100 py-0.5 pr-1 pl-2.5 text-small text-ink">
                {byCode.get(code)?.name ?? code}
                <button
                  type="button"
                  onClick={() => toggle(code, false)}
                  className="inline-flex size-6 items-center justify-center hover:bg-mint-200 pointer-coarse:size-9"
                  aria-label={`Remove ${byCode.get(code)?.name ?? code}`}
                >
                  <X className="size-3.5" />
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <Label htmlFor={filterId} className="sr-only">
          Find a country
        </Label>
        <Input id={filterId} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a country" autoComplete="off" />
        {/* A scroll inside the dialog's own scroll: kept short of the screen on a phone so the
            dialog around it can still be scrolled past it. */}
        <div className="mt-2 max-h-[45dvh] overflow-y-auto border border-line px-3 py-2 sm:max-h-64">
          {groups.length ? (
            groups.map(({ region, list }) => (
              <div key={region} className="py-1.5">
                <p className="mb-0.5 text-small font-semibold tracking-wide text-ink-2 uppercase">{regionName(region)}</p>
                <div className="grid gap-x-4 sm:grid-cols-2">
                  {list.map((p) => (
                    <Checkbox
                      key={p.code}
                      label={p.name}
                      description={coverage(p)}
                      checked={chosen.includes(p.code)}
                      onChange={(e) => toggle(p.code, e.target.checked)}
                    />
                  ))}
                </div>
              </div>
            ))
          ) : (
            <p className="py-2 text-small text-ink-2">No country called “{query}”. WERA can search the countries listed here.</p>
          )}
        </div>
        <p className={cn("mt-1.5 text-small", !chosen.length && !value.locations.length ? "text-amber-700" : "text-ink-2")}>
          {chosen.length
            ? "Boards that only carry jobs elsewhere are skipped, so credits go on searches that can answer."
            : value.locations.length
              ? "No country ticked: WERA works it out from your cities."
              : "Pick a country or add a city — a search needs somewhere to look."}
        </p>
        {errors.countries ? <p className="mt-1 text-small text-red-700">{errors.countries}</p> : null}
      </fieldset>

      <ChipInput
        label="Cities (optional)"
        value={value.locations}
        onChange={(locations) => onChange({ locations })}
        max={8}
        error={errors.locations}
        hint="A city in a country you haven't ticked adds that country — Dubai searches the UAE. Leave empty to search whole countries."
        placeholder="Add a city"
      />

      {value.locations.length ? <CityScope value={value.cityScope} onChange={(cityScope) => onChange({ cityScope })} /> : null}

      <Checkbox
        label="International organisations, any duty station"
        description="UN, WHO, WIPO, WTO, IMF and ITU posts open to any nationality show wherever they are; posts for residents or nationals still follow your countries. Read from the organisations whose terms you've reviewed in config/organisations.yaml."
        checked={value.international}
        onChange={(e) => onChange({ international: e.target.checked })}
      />

      <div>
        <Label htmlFor={remoteId}>Remote</Label>
        <Select id={remoteId} value={value.remote} onChange={(e) => onChange({ remote: e.target.value as SearchProfile["remote"] })}>
          <option value="any">Remote, hybrid or on-site</option>
          <option value="remote_only">Remote only</option>
          <option value="no_remote">On-site or hybrid only</option>
        </Select>
      </div>
    </div>
  );
}

/** "These cities first" or "only these cities": what a named city does to its country. */
export function CityScope({ value, onChange }: { value: SearchProfile["cityScope"]; onChange: (next: SearchProfile["cityScope"]) => void }) {
  const name = useId();
  return (
    <fieldset>
      <legend className="mb-1.5 text-[14px] font-semibold">Your cities</legend>
      <div className="grid gap-1">
        {SCOPES.map((scope) => (
          <label key={scope.id} className="flex cursor-pointer items-start gap-3 py-1">
            <input
              type="radio"
              name={name}
              className="mt-1 size-4 shrink-0 cursor-pointer accent-forest-700"
              checked={value === scope.id}
              onChange={() => onChange(scope.id)}
            />
            <span className="min-w-0">
              <span className="block text-[15px] font-semibold">{scope.label}</span>
              <span className="block text-small text-ink-2">{scope.description}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
