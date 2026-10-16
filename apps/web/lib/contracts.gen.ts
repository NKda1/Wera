// Generated from the agent's Pydantic models (packages/schemas/json). Do not edit.
// Regenerate: uv run --project services/agent wera schemas && pnpm --filter web gen:contracts

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
export type Channel = "board_search" | "email_alert" | "watchlist" | "manual";
export type IntakeChannel = "board_search" | "email_alert" | "watchlist";
export type YearMonth = string;
export type Seniority = "intern" | "graduate" | "junior" | "entry" | "mid";
export type JobStatus = "new" | "shortlisted" | "in_application" | "snoozed" | "rejected" | "hidden" | "closed" | "archived";
export type SkillLevel = "familiar" | "proficient" | "expert";
export type Recency = "24h" | "7d" | "30d";
export type SecretStatus = "set" | "missing" | "invalid";

export interface AnswerItem {
  answer: string | null;
  id: string;
  question: string;
  sensitive: boolean;
  sourceIds: string[];
}
export interface ApiErrorBody {
  error: ErrorDetail;
}
export interface ErrorDetail {
  code: string;
  fields?: {
    [k: string]: string;
  };
  message: string;
}
export interface Application {
  approvedAt?: string;
  changeNote?: string;
  createdAt: string;
  draftStartedAt?: string;
  followUpAt?: string;
  id: string;
  jobId: string;
  notes?: string;
  pack?: Pack;
  status: ApplicationStatus;
  submittedAt?: string;
}
export interface Pack {
  answers: AnswerItem[];
  ats: AtsReport;
  cv: TailoredCV;
  letter: CoverLetter;
}
export interface AtsReport {
  attempt: number;
  coverage: number;
  formatChecks: FormatCheck[];
  keywords: AtsKeyword[];
  pages: number;
}
export interface FormatCheck {
  id: string;
  message: string;
  pass: boolean;
}
export interface AtsKeyword {
  inMasterCv: boolean;
  matched: boolean;
  term: string;
  weight: "required" | "nice";
}
export interface TailoredCV {
  cvVersion: number;
  guard: GuardResult;
  jobId: string;
  omittedBulletIds: string[];
  sections: CvSection[];
  skills: string[];
  summary: Cited;
}
export interface GuardResult {
  pass: boolean;
  repaired: boolean;
  violations: Violation[];
}
export interface Violation {
  detail: string;
  location: string;
  rule: string;
}
export interface CvSection {
  items: CvItem[];
  kind: "experience" | "projects";
}
export interface CvItem {
  bullets: Cited[];
  roleId: string;
}
export interface Cited {
  editedByUser?: boolean;
  sourceIds: string[];
  text: string;
}
export interface CoverLetter {
  companyFact: CompanyFact;
  greeting: string;
  guard: GuardResult;
  jobId: string;
  paragraphs: Cited[];
  signoff: string;
  wordCount: number;
}
export interface CompanyFact {
  quote: string;
}
export interface ApplicationJob {
  applyUrl?: string;
  company: string;
  fitTotal: number;
  id: string;
  linkedinUrl?: string;
  location: Location;
  remote: ("remote" | "hybrid" | "onsite") | null;
  title: string;
}
export interface Location {
  city?: string;
  country?: string;
  raw: string;
}
export interface ApplicationList {
  items: ApplicationListItem[];
}
export interface ApplicationListItem {
  approvedAt?: string;
  changeNote?: string;
  coverage?: number;
  createdAt: string;
  draft?: DraftProgress;
  draftStartedAt?: string;
  followUpAt?: string;
  id: string;
  job: ApplicationJob;
  jobId: string;
  notes?: string;
  pack?: Pack;
  status: ApplicationStatus;
  submittedAt?: string;
}
export interface DraftProgress {
  progress: number;
  step: DraftStep;
}
export interface ApplicationWithJob {
  approvedAt?: string;
  changeNote?: string;
  createdAt: string;
  draft?: DraftProgress;
  draftStartedAt?: string;
  followUpAt?: string;
  id: string;
  job: ApplicationJob;
  jobId: string;
  notes?: string;
  pack?: Pack;
  status: ApplicationStatus;
  submittedAt?: string;
}
export interface Ats {
  boardToken: string;
  jobId: string;
  vendor: "greenhouse" | "lever" | "ashby";
}
export interface Bullet {
  id: string;
  tags: string[];
  text: string;
}
export interface ChannelSummary {
  channel: IntakeChannel;
  detail: string;
  enabled: boolean;
  stats: string;
}
/**
 * CV-06: stored separately from the CV and never sent to a model.
 */
export interface Contact {
  address?: string;
  email: string;
  phone?: string;
}
export interface CvLine {
  text: string;
  where: string;
}
export interface CvVersion {
  version: number;
}
export interface Education {
  degree: string;
  end?: YearMonth;
  id: string;
  institution: string;
  notes?: Bullet[];
  start?: YearMonth;
}
export interface Estimate {
  companies: number;
  credits: number;
  demo: boolean;
  pages: number;
  queries: number;
  usd: number;
}
/**
 * Sub-scores as produced by the scoring model. The total is never model output (MATCH-04).
 */
export interface FitEvidence {
  adjacent: SubScore;
  core: SubScore;
  cvBulletIds: string[];
  eligibility: SubScore;
  experience: SubScore;
  jobQuote: string;
  keywords: JobKeyword[];
  model: string;
  reason: string;
}
export interface SubScore {
  cvBulletIds: string[];
  jobQuote: string;
  score: number;
}
export interface JobKeyword {
  term: string;
  weight: "required" | "nice";
}
export interface FitReport {
  adjacent: SubScore;
  core: SubScore;
  cvBulletIds: string[];
  eligibility: SubScore;
  experience: SubScore;
  freshness: number;
  jobId: string;
  jobQuote: string;
  keywords: JobKeyword[];
  model: string;
  reason: string;
  roughOnly: boolean;
  total: number;
}
export interface FitWeights {
  adjacent: number;
  core: number;
  eligibility: number;
  experience: number;
  freshness: number;
}
export interface FoundCounts {
  board: number;
  inbox: number;
  total: number;
  watchlist: number;
}
export interface InboxSettings {
  emailsThisWeek: number;
  folder: string;
  label: string;
  lastCheckedAt: string;
  senders: string[];
}
export interface IntakeRun {
  channel: IntakeChannel;
  creditsUsed: number;
  errors: RunError[];
  finishedAt?: string;
  id: string;
  newJobs: number;
  progress: number;
  queries: number;
  results: number;
  startedAt: string;
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled";
}
export interface RunError {
  code: string;
  message: string;
}
export interface Job {
  applyUrl?: string;
  ats?: Ats;
  company: string;
  dedupeKey: string;
  descriptionMd: string | null;
  descriptionStatus: "full" | "partial" | "missing";
  firstSeen: string;
  hiddenReason?: string;
  id: string;
  language: string;
  lastSeen: string;
  linkedinUrl?: string;
  location: Location;
  postedAt?: string;
  remote: ("remote" | "hybrid" | "onsite") | null;
  salary?: Salary;
  seniority?: Seniority;
  snoozedUntil?: string;
  sources: JobSource[];
  status: JobStatus;
  title: string;
}
export interface Salary {
  currency: string;
  max?: number;
  min?: number;
  period: "year" | "month" | "day" | "hour";
}
export interface JobSource {
  channel: Channel;
  detail: string;
  seenAt: string;
}
export interface JobList {
  items: ScoredJob[];
  next: null;
}
export interface ScoredJob {
  applyUrl?: string;
  ats?: Ats;
  company: string;
  dedupeKey: string;
  descriptionMd: string | null;
  descriptionStatus: "full" | "partial" | "missing";
  firstSeen: string;
  fit: FitReport;
  hiddenReason?: string;
  id: string;
  language: string;
  lastSeen: string;
  linkedinUrl?: string;
  location: Location;
  postedAt?: string;
  remote: ("remote" | "hybrid" | "onsite") | null;
  salary?: Salary;
  seniority?: Seniority;
  snoozedUntil?: string;
  sources: JobSource[];
  status: JobStatus;
  title: string;
}
export interface Language {
  level: string;
  name: string;
}
export interface Link {
  label: string;
  url: string;
}
export interface MasterCV {
  education: Education[];
  experience: Role[];
  languages: Language[];
  person: Person;
  projects: Role[];
  skills: Skill[];
  summary?: Summary;
  version: number;
}
export interface Role {
  bullets: Bullet[];
  end: YearMonth | "present";
  id: string;
  location?: string;
  org: string;
  start: YearMonth;
  title: string;
}
export interface Person {
  headline?: string;
  links: Link[];
  location: string;
  name: string;
}
export interface Skill {
  aliases?: string[];
  level?: SkillLevel;
  name: string;
}
export interface Summary {
  id: string;
  text: string;
}
export interface NavCounts {
  demoMode: boolean;
  jobs: number;
  review: number;
}
export interface ProfileChannels {
  boardSearch: boolean;
  inbox: boolean;
  watchlist: boolean;
}
export interface RunIds {
  runIds: string[];
}
export interface SearchProfile {
  channels: ProfileChannels;
  exclude: string[];
  generatedFromCvVersion: number;
  locations: string[];
  recency: Recency;
  remote: "any" | "remote_only" | "no_remote";
  seniority: Seniority[];
  sites: string[];
  skills: string[];
  titles: string[];
  updatedAt: string;
}
export interface SecretsStatus {
  firecrawl: SecretStatus;
  inbox: SecretStatus;
  llm: SecretStatus;
}
export interface Settings {
  baseUrl: string;
  cvPages: 1 | 2;
  demoMode: boolean;
  inbox: InboxSettings;
  models: TierModels;
  redactContact: boolean;
  spentThisWeekUsd: number;
  weeklyBudgetUsd: number;
  weights: FitWeights;
}
export interface TierModels {
  fast: string;
  scoring: string;
  writing: string;
}
export interface SettingsResponse {
  baseUrl: string;
  cvPages: 1 | 2;
  demoMode: boolean;
  inbox: InboxSettings;
  llmLastCheckedAt?: string;
  llmProvider: string;
  models: TierModels;
  redactContact: boolean;
  secrets: SecretsStatus;
  spentThisWeekUsd: number;
  weeklyBudgetUsd: number;
  weights: FitWeights;
}
export interface ShortlistMeta {
  activeRunIds: string[];
  channels: ChannelSummary[];
  found: FoundCounts;
  hidden: ScoredJob[];
  newJobIds: string[];
  rejected: number;
  snoozed: number;
}
export interface SiteOption {
  domain: string;
  name: string;
  reviewed: boolean;
}
export interface TestResult {
  code?: string;
  message: string;
  ok: boolean;
}
