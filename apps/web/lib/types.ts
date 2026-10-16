// Data contracts from SPEC §6. The canonical source will be JSON Schema in
// packages/schemas; until then these types are the single source for the web app.

export type YearMonth = `${number}-${number}`;

export interface Bullet {
  id: string; // "exp-itu-01", stable forever
  text: string;
  tags: string[];
}

export interface Role {
  id: string;
  title: string;
  org: string;
  location?: string;
  start: YearMonth;
  end: YearMonth | "present";
  bullets: Bullet[];
}

export type SkillLevel = "familiar" | "proficient" | "expert";

export interface MasterCV {
  version: number;
  person: { name: string; headline?: string; location: string; links: { label: string; url: string }[] };
  summary?: { id: string; text: string };
  experience: Role[];
  projects: Role[];
  education: {
    id: string;
    degree: string;
    institution: string;
    start?: YearMonth;
    end?: YearMonth;
    notes?: Bullet[];
  }[];
  skills: { name: string; aliases?: string[]; level?: SkillLevel }[];
  languages: { name: string; level: string }[];
}

/** CV-06: stored separately from the CV and never sent to a model. */
export interface Contact {
  email: string;
  phone?: string;
  address?: string;
}

export type Seniority = "intern" | "graduate" | "junior" | "entry" | "mid";
export type Recency = "24h" | "7d" | "30d";

export interface SearchProfile {
  titles: string[]; // 1–8
  skills: string[]; // 1–15
  locations: string[]; // 1–8
  remote: "any" | "remote_only" | "no_remote";
  seniority: Seniority[];
  exclude: string[];
  sites: string[];
  recency: Recency;
  channels: { boardSearch: boolean; inbox: boolean; watchlist: boolean };
  generatedFromCvVersion: number;
  updatedAt: string;
}

export type Channel = "board_search" | "email_alert" | "watchlist" | "manual";

export type JobStatus =
  | "new"
  | "shortlisted"
  | "in_application"
  | "snoozed"
  | "rejected"
  | "hidden"
  | "closed"
  | "archived";

export interface Job {
  id: string;
  dedupeKey: string;
  title: string;
  company: string;
  location: { city?: string; country?: string; raw: string };
  remote: "remote" | "hybrid" | "onsite" | null;
  salary?: { min?: number; max?: number; currency: string; period: "year" | "month" | "day" | "hour" };
  descriptionMd: string | null;
  descriptionStatus: "full" | "partial" | "missing";
  applyUrl?: string;
  ats?: { vendor: "greenhouse" | "lever" | "ashby"; boardToken: string; jobId: string };
  linkedinUrl?: string; // MAIL-07: stored, never fetched
  sources: { channel: Channel; detail: string; seenAt: string }[];
  language: string;
  postedAt?: string;
  firstSeen: string;
  lastSeen: string;
  status: JobStatus;
  snoozedUntil?: string;
  hiddenReason?: string;
  seniority?: Seniority;
}

export interface SubScore {
  score: number; // 0–10
  jobQuote: string;
  cvBulletIds: string[];
}

/** Sub-scores as produced by the scoring model. The total is never model output (MATCH-04). */
export interface FitEvidence {
  core: SubScore;
  adjacent: SubScore;
  experience: SubScore;
  eligibility: SubScore;
  /** One-line verdict shown on the job card (MATCH-03). */
  reason: string;
  jobQuote: string;
  cvBulletIds: string[];
  keywords: { term: string; weight: "required" | "nice" }[];
  model: string;
}

export interface FitReport extends FitEvidence {
  jobId: string;
  freshness: number; // computed from postedAt
  total: number; // computed in code, §8.2
  roughOnly: boolean;
}

export interface FitWeights {
  core: number;
  adjacent: number;
  experience: number;
  eligibility: number;
  freshness: number;
}

export type ScoredJob = Job & { fit: FitReport };

// ---- Pack documents (§6.6) ----

export interface Cited {
  text: string;
  sourceIds: string[];
  editedByUser?: boolean; // G2-02
}

export interface GuardResult {
  pass: boolean;
  violations: { location: string; rule: string; detail: string }[];
  repaired: boolean;
}

export interface TailoredCV {
  jobId: string;
  cvVersion: number;
  summary: Cited;
  sections: { kind: "experience" | "projects"; items: { roleId: string; bullets: Cited[] }[] }[];
  skills: string[];
  omittedBulletIds: string[];
  guard: GuardResult;
}

export interface CoverLetter {
  jobId: string;
  greeting: string;
  paragraphs: Cited[];
  signoff: string;
  companyFact: { quote: string };
  wordCount: number;
  guard: GuardResult;
}

export interface AtsReport {
  keywords: { term: string; weight: "required" | "nice"; matched: boolean; inMasterCv: boolean }[];
  coverage: number; // 0–1
  formatChecks: { id: string; pass: boolean; message: string }[];
  pages: number;
  attempt: number;
}

export interface AnswerItem {
  id: string;
  question: string;
  answer: string | null; // null = "Your answer needed" (ANS-02/03)
  sourceIds: string[];
  sensitive: boolean;
}

export interface Pack {
  cv: TailoredCV;
  letter: CoverLetter;
  ats: AtsReport;
  answers: AnswerItem[];
}

// ---- Applications (§6.7, §7.2) ----

export type ApplicationStatus =
  | "drafting"
  | "ready_for_review"
  | "changes_requested"
  | "approved"
  | "submitted"
  | "interview"
  | "offer"
  | "rejected"
  | "discarded"
  | "withdrawn";

export type DraftStep = "cv" | "letter" | "ats";

export interface Application {
  id: string;
  jobId: string;
  status: ApplicationStatus;
  createdAt: string;
  draftStartedAt?: string;
  changeNote?: string;
  pack?: Pack;
  approvedAt?: string;
  submittedAt?: string;
  followUpAt?: string;
  notes?: string;
}

export type ApplicationWithJob = Application & {
  job: Pick<Job, "id" | "title" | "company" | "location" | "remote" | "applyUrl" | "linkedinUrl"> & { fitTotal: number };
  draft?: { step: DraftStep; progress: number };
};

// ---- Intake runs (§6.7) ----

export type IntakeChannel = "board_search" | "email_alert" | "watchlist";

export interface IntakeRun {
  id: string;
  channel: IntakeChannel;
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled";
  startedAt: string;
  finishedAt?: string;
  progress: number; // 0–1, for the UI
  queries: number;
  results: number;
  newJobs: number;
  creditsUsed: number;
  errors: { code: string; message: string }[];
}

export interface ChannelSummary {
  channel: IntakeChannel;
  detail: string;
  stats: string;
  enabled: boolean;
}

// ---- Settings (§5.15) ----

export type SecretStatus = "set" | "missing" | "invalid";

export interface Settings {
  demoMode: boolean;
  redactContact: boolean;
  weeklyBudgetUsd: number;
  spentThisWeekUsd: number;
  cvPages: 1 | 2;
  baseUrl: string;
  models: { fast: string; scoring: string; writing: string };
  weights: FitWeights;
  inbox: { label: string; folder: string; lastCheckedAt: string; emailsThisWeek: number; senders: string[] };
}

export interface SettingsResponse extends Settings {
  secrets: { llm: SecretStatus; firecrawl: SecretStatus; inbox: SecretStatus };
  llmProvider: string;
  llmLastCheckedAt?: string;
}

export interface NavCounts {
  jobs: number;
  review: number;
}

export interface ApiError {
  error: { code: string; message: string };
}
