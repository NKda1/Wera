import "server-only";

import { z } from "zod";
import { ApiError } from "./http";

const yearMonth = z.string().regex(/^\d{4}-\d{2}$/, "Use YYYY-MM");
const bullet = z.object({ id: z.string().min(1), text: z.string(), tags: z.array(z.string()) });
const role = z.object({
  id: z.string().min(1),
  title: z.string().min(1, "Add a title"),
  org: z.string().min(1, "Add an organisation"),
  location: z.string().optional(),
  start: yearMonth,
  end: z.union([yearMonth, z.literal("present")]),
  bullets: z.array(bullet),
});

export const MasterCvSchema = z.object({
  version: z.number().int().nonnegative(),
  person: z.object({
    name: z.string().min(1, "Add your name"),
    headline: z.string().optional(),
    location: z.string(),
    links: z.array(z.object({ label: z.string(), url: z.string() })),
  }),
  summary: z.object({ id: z.string().min(1), text: z.string() }).optional(),
  experience: z.array(role),
  projects: z.array(role),
  education: z.array(
    z.object({
      id: z.string().min(1),
      degree: z.string().min(1, "Add a degree"),
      institution: z.string().min(1, "Add an institution"),
      start: yearMonth.optional(),
      end: yearMonth.optional(),
      notes: z.array(bullet).optional(),
    }),
  ),
  skills: z.array(
    z.object({
      name: z.string().min(1),
      aliases: z.array(z.string()).optional(),
      level: z.enum(["familiar", "proficient", "expert"]).optional(),
    }),
  ),
  languages: z.array(z.object({ name: z.string(), level: z.string() })),
});

export const ContactSchema = z.object({
  email: z.string(),
  phone: z.string().optional(),
  address: z.string().optional(),
});

/** PRF-03 limits; messages name the field. */
export const ProfileSchema = z
  .object({
    titles: z.array(z.string().min(1)).min(1, "Add at least 1 target title").max(8, "Use at most 8 target titles"),
    skills: z.array(z.string().min(1)).min(1, "Add at least 1 skill").max(15, "Use at most 15 skills"),
    locations: z.array(z.string().min(1)).min(1, "Add at least 1 location").max(8, "Use at most 8 locations"),
    remote: z.enum(["any", "remote_only", "no_remote"]),
    seniority: z.array(z.enum(["intern", "graduate", "junior", "entry", "mid"])),
    exclude: z.array(z.string().min(1)),
    sites: z.array(z.string().min(1)),
    recency: z.enum(["24h", "7d", "30d"]),
    channels: z.object({ boardSearch: z.boolean(), inbox: z.boolean(), watchlist: z.boolean() }),
    generatedFromCvVersion: z.number(),
    updatedAt: z.string(),
  })
  .refine((p) => p.channels.boardSearch || p.channels.inbox || p.channels.watchlist, {
    message: "Turn on at least one channel",
    path: ["channels"],
  });

export const SettingsPatchSchema = z
  .object({
    redactContact: z.boolean(),
    weeklyBudgetUsd: z.number().min(0, "Budget can't be negative").max(1000, "Budget is capped at $1,000 a week"),
    cvPages: z.union([z.literal(1), z.literal(2)]),
    baseUrl: z.url("Enter a full URL, like https://openrouter.ai/api/v1"),
    models: z.object({
      fast: z.string().min(1, "Pick a model"),
      scoring: z.string().min(1, "Pick a model"),
      writing: z.string().min(1, "Pick a model"),
    }),
    weights: z.object({
      core: z.number().min(0),
      adjacent: z.number().min(0),
      experience: z.number().min(0),
      eligibility: z.number().min(0),
      freshness: z.number().min(0),
    }),
    inbox: z.object({ senders: z.array(z.string().min(1)) }),
  })
  .partial();

export const DecisionSchema = z.object({
  action: z.enum(["approve", "snooze", "reject", "unhide"]),
  days: z.union([z.literal(1), z.literal(3), z.literal(7)]).optional(),
  reason: z.string().max(200).optional(),
  force: z.boolean().optional(),
});

export const ApplicationPatchSchema = z.object({
  status: z
    .enum(["drafting", "ready_for_review", "changes_requested", "approved", "submitted", "interview", "offer", "rejected", "discarded", "withdrawn"])
    .optional(),
  notes: z.string().max(4000).optional(),
  followUpAt: z.string().nullable().optional(),
});

export const DocumentPatchSchema = z.object({
  target: z.string().min(1),
  text: z.string().max(4000).optional(),
  bulletId: z.string().optional(),
});

export const ImportSchema = z.union([z.object({ url: z.string().min(1) }), z.object({ text: z.string().min(20, "Paste the full description") })]);

export function parse<T>(schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (result.success) return result.data;
  const fields: Record<string, string> = {};
  for (const issue of result.error.issues) {
    const key = issue.path.join(".") || "_";
    fields[key] ??= issue.message;
  }
  const first = Object.entries(fields)[0];
  throw new ApiError(422, "VALIDATION", first ? `${first[0]}: ${first[1]}` : "Invalid input", fields);
}
