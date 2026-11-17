// Generated from the agent's Pydantic models (packages/schemas/json). Do not edit.
// Regenerate: uv run --project services/agent wera schemas && pnpm --filter web gen:contracts

export type Seniority = "intern" | "graduate" | "junior" | "entry" | "mid" | "senior" | "lead" | "principal";
export type KeywordCategory = "hard_skill" | "methodology" | "tool";
export type HookType = "Introduction" | "Core-Metric" | "Company-Mission" | "Industry-Problem" | "Referral";
export type LetterSection = "opening_hook" | "core_value_proposition" | "strategic_alignment_and_culture" | "call_to_action_and_close";
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
export type DraftStep = "cv" | "letter" | "ats" | "answers";
export type Channel = "board_search" | "email_alert" | "watchlist" | "manual";
export type SearchChannel = "board_search" | "email_alert" | "watchlist";
export type YearMonth = string;
export type IntakeChannel = "board_search" | "email_alert" | "watchlist" | "rescore" | "score_more";
export type RunPhase =
  | "queued"
  | "discovering"
  | "planning"
  | "harvesting"
  | "searching"
  | "scraping"
  | "feeds"
  | "reading"
  | "collapsing"
  | "filtering"
  | "scoring"
  | "closing"
  | "done";
export type JobStatus = "new" | "shortlisted" | "in_application" | "snoozed" | "rejected" | "hidden" | "closed" | "archived";
export type Liveness = "open" | "unverified" | "unreachable" | "gone";
export type Urgency = "today" | "this_week" | "two_weeks" | "older" | "unknown";
export type SkillLevel = "familiar" | "proficient" | "expert";
export type PostedPrecision = "exact" | "approximate" | "unknown";
export type Recency = "24h" | "7d" | "21d";
export type SecretStatus = "set" | "missing" | "invalid";
export type SuggestionKind = "exclude_term" | "remove_title" | "add_title" | "add_location";

/**
 * A line that states a number, with the numbers isolated from it.
 */
export interface Achievement {
  bulletId: string;
  metrics: string[];
  text: string;
}
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
  analysis?: JobAnalysis;
  answers: AnswerItem[];
  ats: AtsReport;
  cv: TailoredCV;
  letter: CoverLetter;
}
/**
 * The job-description rubric, filled in for the job a pack is written for.
 *
 * Assembled in `nodes.analyse` from three sources so that each part comes from the one that can
 * be trusted with it: the role title and company are WERA's own record of the job, the matrix
 * is `keywords.extract`'s (already held to the post), and only the department, industry,
 * seniority and strategic context are read by `job.analyse`.
 */
export interface JobAnalysis {
  atsKeywordMatrix: KeywordMatrix;
  roleProfile: RoleProfile;
  strategicContext: StrategicContext;
}
/**
 * The post's terms, grouped. The first three are the checkable keywords coverage measures;
 * `soft_skills_and_culture` is display and letter context, and never counted.
 */
export interface KeywordMatrix {
  hardSkills: string[];
  methodologiesAndFrameworks: string[];
  softSkillsAndCulture: string[];
  toolsAndSoftware: string[];
}
export interface RoleProfile {
  companyName: string;
  department: string | null;
  experienceLevel: Seniority | null;
  industryName: string | null;
  targetRoleTitle: string;
}
/**
 * Why this hire exists, in the post's own words. Every field is a verbatim excerpt of the
 * post or null — see `llm_outputs.QuotedContext`, which is what enforces it.
 */
export interface StrategicContext {
  companyInitiativeOrMarket: string | null;
  companyProblemToSolve: string | null;
  companyValueOrPillar: string | null;
  primaryJobRequirement: string | null;
  upcomingCompanyGoal: string | null;
}
export interface AtsReport {
  backedCoverage: number;
  bestScore?: number;
  coverage: number;
  formatChecks: FormatCheck[];
  keywords: AtsKeyword[];
  letterCoverage?: number;
  pages: number;
  parts: AtsPart[];
  score?: number;
  suggestedSkills: string[];
}
export interface FormatCheck {
  id: string;
  message: string;
  pass: boolean;
}
export interface AtsKeyword {
  category?: KeywordCategory;
  inLetter?: boolean;
  inMasterCv: boolean;
  matched: boolean;
  term: string;
  weight: "required" | "nice";
}
/**
 * One part of the ATS score (ATSC-07): its points out of its share of 100, and why.
 */
export interface AtsPart {
  detail: string;
  id: "keywords" | "evidence" | "alignment" | "structure" | "length";
  label: string;
  max: number;
  points: number;
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
  composed?: boolean;
  editedByUser?: boolean;
  sourceIds: string[];
  text: string;
}
export interface CoverLetter {
  companyFact: CompanyFact;
  greeting: string;
  guard: GuardResult;
  hookType?: HookType;
  jobId: string;
  paragraphs: Paragraph[];
  signoff: string;
  wordCount: number;
}
export interface CompanyFact {
  policyQuote: string;
  quote: string;
}
/**
 * A letter paragraph, and which part of the template it is doing.
 *
 * Both optional, because a letter drafted before the template has no sections to report. A
 * paragraph the person rewrites keeps its section: it is still the opening, or still the close.
 */
export interface Paragraph {
  composed?: boolean;
  editedByUser?: boolean;
  section?: LetterSection;
  sourceIds: string[];
  text: string;
  theme?: string;
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
  vendor: "greenhouse" | "lever" | "ashby" | "un_careers" | "taleo" | "workday" | "successfactors";
}
export interface Bullet {
  id: string;
  tags: string[];
  text: string;
}
export interface ChannelSummary {
  channel: SearchChannel;
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
export interface CoreCompetencies {
  methodologies: string[];
  primaryDomains: string[];
  technicalStack: string[];
}
export interface CvLine {
  text: string;
  where: string;
}
export interface CvProfile {
  coreCompetencies: CoreCompetencies;
  cvVersion: number;
  identity: Identity;
  judged: boolean;
  potentialNarrativeBridges: NarrativeBridges;
  professionalHistory: RoleHistory[];
}
export interface Identity {
  applicantCity: string | null;
  applicantLinkedinUrl: string | null;
  applicantName: string;
  applicantPortfolioUrl: string | null;
  applicantState: string | null;
}
/**
 * `addressed_gaps` is not here: a gap is a gap against a particular post, so it belongs to
 * the pack written for one, not to the CV.
 */
export interface NarrativeBridges {
  strongestCrossFunctionalTrait: Trait | null;
}
export interface Trait {
  bulletIds: string[];
  trait: string;
}
export interface RoleHistory {
  companyName: string;
  duration: string;
  impliedAchievements: ImpliedAchievement[];
  quantifiableAchievements: Achievement[];
  roleId: string;
  roleTitle: string;
  utilizedToolsAndFrameworks: string[];
}
/**
 * A line that implies a result without a number — for the applicant to quantify, if true.
 */
export interface ImpliedAchievement {
  bulletId: string;
  phrase: string;
}
export interface CvVersion {
  clearedJobs: number;
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
  culture: string[];
  cvBulletIds: string[];
  eligibility: SubScore;
  experience: SubScore;
  jobQuote: string;
  keywords: JobKeyword[];
  model: string;
  reason: string;
  rubric: string | null;
}
export interface SubScore {
  cvBulletIds: string[];
  jobQuote: string;
  score: number;
}
export interface JobKeyword {
  category?: KeywordCategory;
  term: string;
  weight: "required" | "nice";
}
export interface FitReport {
  adjacent: SubScore;
  core: SubScore;
  culture: string[];
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
  rubric: string | null;
  stale: boolean;
  total: number;
}
export interface FitWeights {
  adjacent: number;
  core: number;
  eligibility: number;
  experience: number;
  freshness: number;
}
/**
 * SRCH-25: the run that first added a job — written once, when the job is inserted.
 *
 * What the Jobs screen's "New" tag and its origin label ("Search 11 Nov") read. It replaces a
 * single list of the last run's job ids, which every run overwrote — so a job lost its "new"
 * mark the moment any other run finished, inbox and watchlist runs included, whether or not
 * anyone had looked at it.
 */
export interface FoundBy {
  at: string;
  channel: Channel;
  runId?: string;
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
  lastCheckedAt: string | null;
  senders: string[];
}
export interface IntakeRun {
  batchSize: number;
  channel: IntakeChannel;
  creditsSaved: number;
  creditsUsed: number;
  detail: string | null;
  done: number;
  errors: RunError[];
  fallbackPages: number;
  finishedAt?: string;
  id: string;
  localPages: number;
  newJobs: number;
  phase: RunPhase;
  progress: number;
  queries: number;
  queued: number;
  results: number;
  startedAt: string;
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled";
  total: number;
}
export interface RunError {
  code: string;
  message: string;
}
/**
 * What an international organisation's own system says about a post (SRCH-34).
 */
export interface InternationalPost {
  family?: string;
  grade?: string;
  org: string;
  recruitment?: "international" | "local";
}
export interface Job {
  applyUrl?: string;
  ats?: Ats;
  closedAt?: string;
  closesAt?: string;
  company: string;
  dedupeKey: string;
  descriptionMd: string | null;
  descriptionStatus: "full" | "partial" | "missing";
  firstSeen: string;
  foundBy?: FoundBy;
  hiddenReason?: string;
  id: string;
  international?: InternationalPost;
  language: string;
  lastCheckedAt?: string;
  lastConfirmedAt?: string;
  lastSeen: string;
  linkedinUrl?: string;
  location: Location;
  postedAt?: string;
  postedPrecision: "exact" | "approximate" | "unknown";
  remote: ("remote" | "hybrid" | "onsite") | null;
  salary?: Salary;
  seenAt?: string;
  seniority?: Seniority;
  snoozedUntil?: string;
  sources: JobSource[];
  status: JobStatus;
  title: string;
  via?: string;
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
  possiblyClosed: number;
}
export interface ScoredJob {
  applyUrl?: string;
  ats?: Ats;
  closedAt?: string;
  closesAt?: string;
  company: string;
  dedupeKey: string;
  descriptionMd: string | null;
  descriptionStatus: "full" | "partial" | "missing";
  evergreen: boolean;
  firstSeen: string;
  fit: FitReport;
  foundBy?: FoundBy;
  hiddenReason?: string;
  id: string;
  international?: InternationalPost;
  language: string;
  lastCheckedAt?: string;
  lastConfirmedAt?: string;
  lastSeen: string;
  learned?: LearnedRank;
  linkedinUrl?: string;
  liveness: Liveness;
  location: Location;
  possiblyClosed: boolean;
  postedAt?: string;
  postedPrecision: "exact" | "approximate" | "unknown";
  remote: ("remote" | "hybrid" | "onsite") | null;
  salary?: Salary;
  seenAt?: string;
  seniority?: Seniority;
  snoozedUntil?: string;
  sources: JobSource[];
  status: JobStatus;
  title: string;
  urgency: Urgency;
  via?: string;
}
/**
 * MATCH-12: how far a job moved in the order, and why. Never part of `fit.total`.
 */
export interface LearnedRank {
  adjustment: number;
  reason: string;
}
/**
 * SRCH-27: what "Clear jobs" removed — or, before it runs, what it would remove.
 */
export interface JobsCleared {
  applications: number;
  documents: number;
  jobs: number;
}
export interface Language {
  level: string;
  name: string;
}
export interface Link {
  label: string;
  url: string;
}
/**
 * What a regenerated profile would do to one list field (PRF-04).
 */
export interface ListDiff {
  added: string[];
  kept: string[];
  removed: string[];
}
export interface LocalTerm {
  language: string;
  term: string;
  title: string;
}
export interface Market {
  boards: string[];
  country: string;
  languages: string[];
  proposed: string[];
  searchTerms: LocalTerm[];
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
  start: YearMonth | "";
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
  generated?: boolean;
  id: string;
  text: string;
}
export interface NavCounts {
  demoMode: boolean;
  jobs: number;
  review: number;
}
/**
 * SRCH-11: a country the profile can target, and what would search it there.
 *
 * Offered whether or not a reviewed board covers it — the open web search reaches every one,
 * and the job APIs most — so the picker is no longer limited to where the boards happen to be.
 */
export interface PlaceOption {
  boards: string[];
  code: string;
  feeds: string[];
  name: string;
  region: string;
}
/**
 * One search the Strategist will send (SRCH-20, stage 1). Built in code from the parsed CV and
 * the profile, never by a model, and stored on the plan so the queries a search used can be read
 * back — and so a new CV, which makes a new plan, can never search with the last CV's words.
 */
export interface PlannedQuery {
  domain?: string;
  gl: string;
  hl: string;
  page: number;
  q: string;
  source: "open" | "board";
  tier: number;
  title: string;
}
export interface ProfileChannels {
  boardSearch: boolean;
  inbox: boolean;
  watchlist: boolean;
}
/**
 * A proposed profile and what it would change. Nothing is saved until the person PUTs it.
 */
export interface ProfileProposalResult {
  cvVersion: number;
  diff: {
    [k: string]: ListDiff | ScalarDiff;
  };
  proposed: SearchProfile;
}
export interface ScalarDiff {
  from: string;
  to: string;
}
export interface SearchProfile {
  channels: ProfileChannels;
  cityScope: "first" | "only";
  countries: string[];
  exclude: string[];
  generatedFromCvVersion: number;
  international: boolean;
  locations: string[];
  recency: Recency;
  remote: "any" | "remote_only" | "no_remote";
  seniority: Seniority[];
  sites: string[];
  skills: string[];
  titles: string[];
  updatedAt: string;
}
/**
 * A skill the posts ask for that the CV backs in a line but doesn't list as a skill: the
 * cheapest point on the ATS score (ATSC-07), and only the person can decide to claim it.
 */
export interface QuickWin {
  jobs: number;
  line: string;
  share: number;
  term: string;
}
export interface RunIds {
  runIds: string[];
}
/**
 * `PUT /profile`'s answer: the profile, and what it changed on the list when "where" moved.
 */
export interface SavedProfile {
  channels: ProfileChannels;
  cityScope: "first" | "only";
  countries: string[];
  exclude: string[];
  generatedFromCvVersion: number;
  international: boolean;
  locations: string[];
  recency: Recency;
  refiltered: WhereChange | null;
  remote: "any" | "remote_only" | "no_remote";
  seniority: Seniority[];
  sites: string[];
  skills: string[];
  titles: string[];
  updatedAt: string;
}
/**
 * What saving a new "where" did to the jobs already found (16 Nov): the list responds at once,
 * rather than at the next search. `shown` came back from hiding; `hidden` are hidden now.
 */
export interface WhereChange {
  hidden: number;
  shown: number;
}
/**
 * The scheduled job search: when the Strategist and the Harvester run without being asked.
 */
export interface SearchSchedule {
  creditsThisWeek?: number;
  cron: string;
  enabled: boolean;
  nextRunAt?: string;
  weeklyCreditCap?: number;
}
export interface SecretsStatus {
  firecrawl: SecretStatus;
  inbox: SecretStatus;
  llm: SecretStatus;
  serper: "set" | "missing" | "invalid";
}
export interface Settings {
  baseUrl: string;
  cvPages: 1 | 2;
  demoMode: boolean;
  inbox: InboxSettings;
  learnedRanking: boolean;
  models: TierModels;
  redactContact: boolean;
  scheduledIntake: boolean;
  searchSchedule: SearchSchedule;
  spentThisWeekUsd: number;
  weeklyBudgetUsd: number;
  weights: FitWeights;
}
export interface TierModels {
  fast: string;
  scoring: string;
  select: string;
  writing: string;
}
export interface SettingsResponse {
  allowClearJobs: boolean;
  baseUrl: string;
  cvPages: 1 | 2;
  demoMode: boolean;
  inbox: InboxSettings;
  learnedRanking: boolean;
  llmLastCheckedAt?: string;
  llmProvider: string;
  models: TierModels;
  redactContact: boolean;
  scheduledIntake: boolean;
  searchSchedule: SearchSchedule;
  secrets: SecretsStatus;
  spentThisWeekUsd: number;
  weeklyBudgetUsd: number;
  weights: FitWeights;
}
export interface ShortlistMeta {
  activeRunIds: string[];
  awaiting: Job[];
  awaitingScore: number;
  channels: ChannelSummary[];
  findMoreSize: number;
  found: FoundCounts;
  hidden: Job[];
  hiddenTotal: number;
  nearMisses: number;
  rejected: number;
  snoozed: number;
}
export interface SiteOption {
  countries: string[];
  declined: boolean;
  discoveredOn?: string;
  domain: string;
  examples: string[];
  matched?: string;
  name: string;
  proposed: boolean;
  region: string;
  reviewed: boolean;
  robotsOk?: boolean;
}
/**
 * MATCH-13: a change to the search profile that the person's decisions point at. Nothing
 * changes until they accept it (PRF-04).
 */
export interface Suggestion {
  evidence: number;
  id: string;
  kind: SuggestionKind;
  message: string;
  value: string;
}
export interface SuggestionList {
  decisions: number;
  items: Suggestion[];
}
export interface TargetMatrix {
  cvVersion: number;
  field: string;
  generatedAt: string;
  markets: Market[];
  model: string;
  profileKey: string;
  queries: PlannedQuery[];
}
export interface TestResult {
  code?: string;
  message: string;
  ok: boolean;
}
/**
 * One skill the CV doesn't show, and how much the person's market wants it.
 */
export interface Upskill {
  category?: KeywordCategory;
  closeTo: string[];
  examples: UpskillJob[];
  jobs: number;
  previous: number;
  recent: number;
  requiredShare: number;
  score: number;
  share: number;
  spellings: string[];
  term: string;
  trend: "new" | "rising" | "steady" | "falling";
}
export interface UpskillJob {
  company: string;
  id: string;
  title: string;
}
export interface UpskillsReport {
  jobsConsidered: number;
  missing: Upskill[];
  quickWins: QuickWin[];
  windowDays: number;
}
