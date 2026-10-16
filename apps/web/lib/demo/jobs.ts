// Demo jobs. Sub-scores are fixture "model output"; totals are computed in code
// (lib/scoring.ts). With the default weights the top five reproduce the mockup:
// 8.6, 8.1, 7.4, 7.0, 6.2.

import type { Channel, FitEvidence, Job, JobStatus, Seniority, SubScore } from "../types";

export interface DemoJobSeed {
  id: string;
  title: string;
  company: string;
  city: string;
  country: string;
  remote: Job["remote"];
  salary?: Job["salary"];
  postedDaysAgo: number;
  seniority?: Seniority;
  description: string | null;
  ats?: Job["ats"];
  applyUrl?: string;
  linkedinUrl?: string;
  sources: { channel: Channel; detail: string }[];
  evidence: FitEvidence;
  questions?: string[];
  status?: JobStatus;
  hiddenReason?: string;
}

const MODEL = "qwen/qwen3.8-27b";

const s = (score: number, jobQuote = "", cvBulletIds: string[] = []): SubScore => ({ score, jobQuote, cvBulletIds });

const kw = (required: string, nice: string): FitEvidence["keywords"] => [
  ...required.split("|").filter(Boolean).map((term) => ({ term, weight: "required" as const })),
  ...nice.split("|").filter(Boolean).map((term) => ({ term, weight: "nice" as const })),
];

const GREENHOUSE_QUESTIONS = (company: string, country: string) => [
  `Why do you want to work at ${company}?`,
  "Tell us about a time you shipped an LLM feature to production.",
  "Link to your GitHub or portfolio",
  `Are you legally authorised to work in ${country}?`,
  "Will you now or in the future require visa sponsorship?",
  "What are your salary expectations?",
];

/** Today's shortlist (Gate 1). */
export const shortlistSeeds: DemoJobSeed[] = [
  {
    id: "job-fernhill-mle",
    title: "ML Engineer, Graduate",
    company: "Fernhill AI",
    city: "London",
    country: "United Kingdom",
    remote: "hybrid",
    salary: { min: 45000, max: 55000, currency: "GBP", period: "year" },
    postedDaysAgo: 1.8,
    seniority: "graduate",
    description: `## About Fernhill AI
Fernhill AI builds evaluation and guardrail tooling for teams shipping LLM products in regulated industries. Our customers use Fernhill to catch personal data and unsafe output before it reaches production.

## The role
You'll join the platform team as a graduate ML engineer. You'll work in Python, FastAPI and LLM orchestration, building the services that sit between our customers' apps and the models they call.

## What you'll do
- Build and run Python services that scan prompts and responses (PII, policy, jailbreaks)
- Wire up agent workflows with LangGraph and measure them with our model evaluation harness
- Ship REST APIs used by customer teams, with good docs and tests
- Package services with Docker and deploy them with the infra team

## About you
- A degree in CS or similar, graduating 2025 or 2026
- Python and FastAPI; some TypeScript is a plus
- Interest in LLMs, prompting and guardrails
- PostgreSQL or another relational database`,
    ats: { vendor: "greenhouse", boardToken: "fernhillai", jobId: "4471023" },
    applyUrl: "https://job-boards.greenhouse.io/fernhillai/jobs/4471023",
    linkedinUrl: "https://www.linkedin.com/jobs/view/4100000001",
    sources: [{ channel: "email_alert", detail: "linkedin-alert" }],
    evidence: {
      core: s(8.7, "Python, FastAPI and LLM orchestration", ["exp-idsu-01"]),
      adjacent: s(8, "evaluation and guardrail tooling", ["exp-idsu-03"]),
      experience: s(8, "graduating 2025 or 2026", ["exp-idsu-01", "exp-fm-01"]),
      eligibility: s(9, "London, hybrid", []),
      reason: "matches your IDSU PII gateway and agent work.",
      jobQuote: "Python, FastAPI and LLM orchestration",
      cvBulletIds: ["exp-idsu-01", "exp-idsu-02"],
      keywords: kw(
        "Python|FastAPI|LLMs|Agents|PII|Docker|REST APIs",
        "LangGraph|TypeScript|PostgreSQL|Guardrails|Next.js|Prompting|Model evaluation",
      ),
      model: MODEL,
    },
    questions: GREENHOUSE_QUESTIONS("Fernhill AI", "the United Kingdom"),
  },
  {
    id: "job-northwind-swe",
    title: "Software Engineer, AI Platform",
    company: "Northwind Labs",
    city: "London",
    country: "United Kingdom",
    remote: "onsite",
    salary: { min: 50000, max: 60000, currency: "GBP", period: "year" },
    postedDaysAgo: 0.2,
    seniority: "junior",
    description: `## Northwind Labs
Northwind Labs is a 40-person team building an internal AI platform for mid-size logistics companies. We ship weekly and our engineers own features end to end.

## The role
TypeScript, Next.js and Postgres product work on the AI platform: the console our customers use to configure agents, review runs and manage billing.

## You'll
- Build product features across Next.js, a Node API layer and PostgreSQL
- Integrate third-party APIs (Stripe, email, model providers)
- Work with the AI team to expose agent runs in the product

## You have
- 0–2 years of experience shipping a product with real users
- TypeScript and React; Postgres
- Curiosity about LLMs and agents`,
    ats: { vendor: "ashby", boardToken: "northwind", jobId: "b7c1e2" },
    applyUrl: "https://jobs.ashbyhq.com/northwind/b7c1e2",
    sources: [{ channel: "board_search", detail: "welcometothejungle.com" }],
    evidence: {
      core: s(7.8, "TypeScript, Next.js and Postgres product work", ["exp-fm-01"]),
      adjacent: s(9, "Integrate third-party APIs (Stripe, email, model providers)", ["exp-fm-02"]),
      experience: s(7, "0–2 years of experience shipping a product with real users", ["exp-fm-01"]),
      eligibility: s(8, "London", []),
      reason: "matches Fieldmark, built and run end to end.",
      jobQuote: "TypeScript, Next.js and Postgres product work",
      cvBulletIds: ["exp-fm-01", "exp-fm-02"],
      keywords: kw("TypeScript|Next.js|PostgreSQL|REST APIs|React", "Stripe|LLMs|Agents|Node.js"),
      model: MODEL,
    },
  },
  {
    id: "job-kestrel-jaie",
    title: "Junior AI Engineer",
    company: "Kestrel Health",
    city: "Amsterdam",
    country: "Netherlands",
    remote: "hybrid",
    salary: { min: 48000, max: 56000, currency: "EUR", period: "year" },
    postedDaysAgo: 3.9,
    seniority: "junior",
    description: `## About Kestrel Health
Kestrel Health helps hospitals in the Netherlands and Belgium summarise clinical notes safely. Every output is reviewed by a clinician before it is used.

## The role
You'll work on RAG and guardrails for clinical text: retrieval over patient records, redaction of personal data, and checks that stop unsupported claims.

## Requirements
- Python, and experience with LLM APIs
- An understanding of PII handling and privacy
- RAG or vector search experience is a plus
- English required; Dutch is a plus`,
    ats: { vendor: "lever", boardToken: "kestrelhealth", jobId: "5d2a9c" },
    applyUrl: "https://jobs.lever.co/kestrelhealth/5d2a9c",
    sources: [
      { channel: "board_search", detail: "brightnetwork.co.uk" },
      { channel: "email_alert", detail: "indeed-alert" },
    ],
    evidence: {
      core: s(8.4, "Python, and experience with LLM APIs", ["exp-idsu-01"]),
      adjacent: s(7, "redaction of personal data", ["exp-idsu-01"]),
      experience: s(7, "Junior", ["exp-idsu-01"]),
      eligibility: s(6.4, "Amsterdam, hybrid · Dutch is a plus", []),
      reason: "your PII detection work is direct evidence; RAG is new.",
      jobQuote: "RAG and guardrails for clinical text",
      cvBulletIds: ["exp-idsu-01"],
      keywords: kw("Python|LLMs|PII|RAG", "Guardrails|Vector search|Privacy"),
      model: MODEL,
    },
  },
  {
    id: "job-lumen-be",
    title: "Backend Engineer (Python)",
    company: "Lumen Payments",
    city: "Dublin",
    country: "Ireland",
    remote: "onsite",
    salary: { min: 55000, max: 65000, currency: "EUR", period: "year" },
    postedDaysAgo: 6.6,
    seniority: "entry",
    description: `## Lumen Payments
Lumen Payments processes card payments for 3,000 independent retailers across Ireland and the UK.

## The role
Python services and REST APIs for our merchant platform: onboarding, payouts and reconciliation.

## You bring
- Python (FastAPI or Django)
- REST API design, PostgreSQL
- Docker and CI/CD
- Interest in payments; no prior payments experience needed`,
    ats: { vendor: "greenhouse", boardToken: "lumenpayments", jobId: "6120334" },
    applyUrl: "https://job-boards.greenhouse.io/lumenpayments/jobs/6120334",
    sources: [{ channel: "watchlist", detail: "greenhouse:lumenpayments" }],
    evidence: {
      core: s(7, "Python (FastAPI or Django)", ["exp-idsu-01"]),
      adjacent: s(6, "REST API design, PostgreSQL", ["exp-fm-02"]),
      experience: s(8, "no prior payments experience needed", []),
      eligibility: s(7, "Dublin", []),
      reason: "strong fit; the payments domain is new to you.",
      jobQuote: "Python services and REST APIs",
      cvBulletIds: ["exp-idsu-01", "exp-fm-02"],
      keywords: kw("Python|FastAPI|REST APIs|PostgreSQL", "Docker|CI/CD|Stripe"),
      model: MODEL,
    },
    questions: GREENHOUSE_QUESTIONS("Lumen Payments", "Ireland"),
  },
  {
    id: "job-harbour-de",
    title: "Graduate Data Engineer",
    company: "Harbour Analytics",
    city: "London",
    country: "United Kingdom",
    remote: null,
    postedDaysAgo: 1.2,
    seniority: "graduate",
    description: null,
    linkedinUrl: "https://www.linkedin.com/jobs/view/4100000002",
    sources: [{ channel: "email_alert", detail: "linkedin-alert" }],
    evidence: {
      core: s(5.6),
      adjacent: s(5.6),
      experience: s(6.6),
      eligibility: s(5.6),
      reason: "",
      jobQuote: "",
      cvBulletIds: [],
      keywords: [],
      model: MODEL,
    },
  },
  {
    id: "job-brightwater-gse",
    title: "Graduate Software Engineer",
    company: "Brightwater Energy",
    city: "Berlin",
    country: "Germany",
    remote: "hybrid",
    salary: { min: 52000, max: 58000, currency: "EUR", period: "year" },
    postedDaysAgo: 3,
    seniority: "graduate",
    description: `## Brightwater Energy
Brightwater Energy forecasts demand for regional power grids in Germany and Austria.

## The role
Python tooling for grid forecasting teams: data pipelines, internal APIs and dashboards.

## You have
- Python; FastAPI or Flask
- SQL and PostgreSQL
- German is a plus, not required`,
    ats: { vendor: "greenhouse", boardToken: "brightwater", jobId: "7781200" },
    applyUrl: "https://job-boards.greenhouse.io/brightwater/jobs/7781200",
    sources: [{ channel: "board_search", detail: "gradcracker.com" }],
    evidence: {
      core: s(5.8, "Python; FastAPI or Flask", ["exp-idsu-01"]),
      adjacent: s(6),
      experience: s(6),
      eligibility: s(6, "Berlin · German is a plus"),
      reason: "your FastAPI services transfer; the energy domain is new.",
      jobQuote: "Python tooling for grid forecasting teams",
      cvBulletIds: ["exp-idsu-01"],
      keywords: kw("Python|FastAPI|PostgreSQL|SQL", "Pandas|Airflow"),
      model: MODEL,
    },
    questions: GREENHOUSE_QUESTIONS("Brightwater Energy", "Germany"),
  },
  {
    id: "job-tessellate-aie",
    title: "AI Engineer",
    company: "Tessellate",
    city: "Zurich",
    country: "Switzerland",
    remote: "onsite",
    salary: { min: 95000, max: 110000, currency: "CHF", period: "year" },
    postedDaysAgo: 5,
    seniority: "entry",
    description: `## Tessellate
Tessellate builds agent tooling for insurance claims teams.

## The role
LLM evaluation and agent tooling: build the harness we use to test agents before every release.

## You have
- Python and LLM APIs
- Experience with agents or evaluation
- Right to work in Switzerland`,
    ats: { vendor: "ashby", boardToken: "tessellate", jobId: "c19f04" },
    applyUrl: "https://jobs.ashbyhq.com/tessellate/c19f04",
    sources: [{ channel: "watchlist", detail: "ashby:tessellate" }],
    evidence: {
      core: s(7, "Python and LLM APIs", ["exp-idsu-01"]),
      adjacent: s(6, "Experience with agents or evaluation", ["exp-idsu-02"]),
      experience: s(5.2),
      eligibility: s(4, "Right to work in Switzerland"),
      reason: "your agent work fits; a Swiss work permit is needed.",
      jobQuote: "LLM evaluation and agent tooling",
      cvBulletIds: ["exp-idsu-02", "exp-fm-03"],
      keywords: kw("Python|LLMs|Agents|Model evaluation", "LangGraph|Docker"),
      model: MODEL,
    },
  },
  {
    id: "job-orbital-llm",
    title: "Graduate Engineer, LLM Products",
    company: "Orbital Legal",
    city: "London",
    country: "United Kingdom",
    remote: "hybrid",
    salary: { min: 42000, max: 50000, currency: "GBP", period: "year" },
    postedDaysAgo: 6,
    seniority: "graduate",
    description: `## Orbital Legal
Orbital Legal turns contracts into structured data for in-house legal teams.

## The role
Build LLM features in our TypeScript product: extraction, review suggestions and audit trails.

## You have
- TypeScript or Python
- Some exposure to LLM APIs and prompting
- An eye for product detail`,
    ats: { vendor: "ashby", boardToken: "orbitallegal", jobId: "a0e7d1" },
    applyUrl: "https://jobs.ashbyhq.com/orbitallegal/a0e7d1",
    linkedinUrl: "https://www.linkedin.com/jobs/view/4100000003",
    sources: [
      { channel: "email_alert", detail: "linkedin-alert" },
      { channel: "board_search", detail: "brightnetwork.co.uk" },
    ],
    evidence: {
      core: s(6.4, "TypeScript or Python", ["exp-fm-01"]),
      adjacent: s(6),
      experience: s(5),
      eligibility: s(5),
      reason: "Fieldmark shows product range; legal tech is new.",
      jobQuote: "Build LLM features in our TypeScript product",
      cvBulletIds: ["exp-fm-01", "exp-idsu-03"],
      keywords: kw("TypeScript|LLMs|Prompting", "Python|Next.js"),
      model: MODEL,
    },
  },
  {
    id: "job-quayside-be",
    title: "Backend Engineer, Python",
    company: "Quayside Logistics",
    city: "London",
    country: "United Kingdom",
    remote: "hybrid",
    salary: { min: 48000, max: 58000, currency: "GBP", period: "year" },
    postedDaysAgo: 9,
    seniority: "junior",
    description: `## Quayside Logistics
Quayside plans last-mile delivery routes for grocery chains.

## The role
FastAPI services for route planning and driver apps.

## You have
- Python, FastAPI
- PostgreSQL
- Docker`,
    ats: { vendor: "lever", boardToken: "quayside", jobId: "8b3311" },
    applyUrl: "https://jobs.lever.co/quayside/8b3311",
    sources: [{ channel: "board_search", detail: "builtinlondon.uk" }],
    evidence: {
      core: s(6.2, "Python, FastAPI", ["exp-idsu-01"]),
      adjacent: s(5),
      experience: s(6),
      eligibility: s(7),
      reason: "Python and FastAPI match; posted over a week ago.",
      jobQuote: "FastAPI services for route planning",
      cvBulletIds: ["exp-idsu-01"],
      keywords: kw("Python|FastAPI|PostgreSQL", "Docker"),
      model: MODEL,
    },
  },
  {
    id: "job-marlow-mlp",
    title: "Junior ML Platform Engineer",
    company: "Marlow Bio",
    city: "Amsterdam",
    country: "Netherlands",
    remote: "hybrid",
    salary: { min: 50000, max: 60000, currency: "EUR", period: "year" },
    postedDaysAgo: 1.1,
    seniority: "junior",
    description: `## Marlow Bio
Marlow Bio runs protein-design models for research labs.

## The role
Keep our ML platform running: Kubernetes jobs, model serving and Python tooling.

## You have
- Python
- Kubernetes or Docker
- Some MLOps exposure`,
    ats: { vendor: "greenhouse", boardToken: "marlowbio", jobId: "5502281" },
    applyUrl: "https://job-boards.greenhouse.io/marlowbio/jobs/5502281",
    sources: [{ channel: "email_alert", detail: "indeed-alert" }],
    evidence: {
      core: s(5.4, "Python", ["exp-fm-03"]),
      adjacent: s(5.4),
      experience: s(5),
      eligibility: s(5),
      reason: "Python fits; Kubernetes and MLOps are new to you.",
      jobQuote: "Kubernetes jobs, model serving and Python tooling",
      cvBulletIds: ["exp-fm-03"],
      keywords: kw("Python|Kubernetes|Docker", "MLOps|Model serving"),
      model: MODEL,
    },
    questions: GREENHOUSE_QUESTIONS("Marlow Bio", "the Netherlands"),
  },
  {
    id: "job-pennant-int",
    title: "Software Engineer, Integrations",
    company: "Pennant Sports",
    city: "Dublin",
    country: "Ireland",
    remote: "remote",
    salary: { min: 50000, max: 58000, currency: "EUR", period: "year" },
    postedDaysAgo: 10,
    seniority: "entry",
    description: `## Pennant Sports
Pennant runs league management software for amateur sports clubs.

## The role
Stripe and partner API integrations across our TypeScript stack.

## You have
- TypeScript
- REST APIs and webhooks
- Stripe is a plus`,
    ats: { vendor: "lever", boardToken: "pennant", jobId: "2f9a10" },
    applyUrl: "https://jobs.lever.co/pennant/2f9a10",
    sources: [{ channel: "board_search", detail: "welcometothejungle.com" }],
    evidence: {
      core: s(5.6, "TypeScript", ["exp-fm-02"]),
      adjacent: s(6.4, "Stripe and partner API integrations", ["exp-fm-02"]),
      experience: s(5),
      eligibility: s(5),
      reason: "your Fieldmark integrations are direct evidence.",
      jobQuote: "Stripe and partner API integrations",
      cvBulletIds: ["exp-fm-02"],
      keywords: kw("TypeScript|REST APIs|Webhooks", "Stripe"),
      model: MODEL,
    },
  },
  {
    id: "job-cobalt-jbe",
    title: "Junior Backend Engineer",
    company: "Cobalt Grid",
    city: "Berlin",
    country: "Germany",
    remote: "onsite",
    salary: { min: 48000, max: 54000, currency: "EUR", period: "year" },
    postedDaysAgo: 4.5,
    seniority: "junior",
    description: `## Cobalt Grid
Cobalt Grid builds battery-storage control software.

## The role
Go and Python services for device telemetry.

## You have
- Go or Python
- gRPC or REST
- Time-series databases are a plus`,
    ats: { vendor: "greenhouse", boardToken: "cobaltgrid", jobId: "3349012" },
    applyUrl: "https://job-boards.greenhouse.io/cobaltgrid/jobs/3349012",
    sources: [{ channel: "board_search", detail: "gradcracker.com" }],
    evidence: {
      core: s(4.6, "Go or Python", ["exp-idsu-01"]),
      adjacent: s(5),
      experience: s(5.4),
      eligibility: s(5),
      reason: "Python counts; Go and telemetry are new.",
      jobQuote: "Go and Python services for device telemetry",
      cvBulletIds: ["exp-idsu-01"],
      keywords: kw("Go|Python|REST APIs", "gRPC|Time-series databases"),
      model: MODEL,
    },
    questions: GREENHOUSE_QUESTIONS("Cobalt Grid", "Germany"),
  },
];

/** Hard-filtered before any model call (MATCH-01). Kept, hidden, with the reason. */
export const hiddenSeeds: DemoJobSeed[] = [
  {
    id: "job-hidden-senior",
    title: "Senior ML Engineer",
    company: "Fernhill AI",
    city: "London",
    country: "United Kingdom",
    remote: "hybrid",
    postedDaysAgo: 2,
    description: "Senior role: 5+ years building ML systems in production.",
    sources: [{ channel: "board_search", detail: "brightnetwork.co.uk" }],
    status: "hidden",
    hiddenReason: "Title contains an excluded term: Senior",
    evidence: {
      core: s(7),
      adjacent: s(6),
      experience: s(2, "5+ years building ML systems"),
      eligibility: s(9),
      reason: "the stack fits, but it asks for 5+ years.",
      jobQuote: "5+ years building ML systems in production",
      cvBulletIds: ["exp-idsu-01"],
      keywords: kw("Python|LLMs", ""),
      model: MODEL,
    },
  },
  {
    id: "job-hidden-staff",
    title: "Staff Backend Engineer",
    company: "Northwind Labs",
    city: "London",
    country: "United Kingdom",
    remote: "onsite",
    postedDaysAgo: 4,
    description: "Staff-level backend role leading the platform team.",
    sources: [{ channel: "board_search", detail: "welcometothejungle.com" }],
    status: "hidden",
    hiddenReason: "Title contains an excluded term: Staff",
    evidence: {
      core: s(6),
      adjacent: s(5),
      experience: s(1),
      eligibility: s(8),
      reason: "a staff-level role.",
      jobQuote: "leading the platform team",
      cvBulletIds: [],
      keywords: kw("TypeScript", ""),
      model: MODEL,
    },
  },
  {
    id: "job-hidden-munich",
    title: "AI Engineer",
    company: "Isar Robotics",
    city: "Munich",
    country: "Germany",
    remote: "onsite",
    postedDaysAgo: 3,
    description: "On-site AI engineer role in Munich.",
    sources: [{ channel: "email_alert", detail: "indeed-alert" }],
    status: "hidden",
    hiddenReason: "Location not in your profile: Munich (on-site)",
    evidence: {
      core: s(7),
      adjacent: s(6),
      experience: s(6),
      eligibility: s(4),
      reason: "a good stack match outside your locations.",
      jobQuote: "AI engineer",
      cvBulletIds: ["exp-idsu-02"],
      keywords: kw("Python|LLMs", ""),
      model: MODEL,
    },
  },
];

/** Found by the first "Search now" in demo mode (recorded Firecrawl results). */
export const firstRunSeeds: DemoJobSeed[] = [
  {
    id: "job-oakline-aie",
    title: "AI Engineer, Graduate Programme",
    company: "Oakline Robotics",
    city: "London",
    country: "United Kingdom",
    remote: "hybrid",
    salary: { min: 46000, max: 52000, currency: "GBP", period: "year" },
    postedDaysAgo: 0.1,
    seniority: "graduate",
    description: `## Oakline Robotics
Oakline Robotics builds warehouse robots that work alongside people, and an LLM assistant that helps site managers plan shifts.

## The role
Graduate programme: rotate between the assistant team (Python, FastAPI, LLM guardrails) and the fleet API team.

## You have
- Python and FastAPI
- Interest in LLMs and agents
- PostgreSQL`,
    ats: { vendor: "greenhouse", boardToken: "oaklinerobotics", jobId: "9001442" },
    applyUrl: "https://job-boards.greenhouse.io/oaklinerobotics/jobs/9001442",
    sources: [{ channel: "board_search", detail: "brightnetwork.co.uk" }],
    evidence: {
      core: s(7.6, "Python, FastAPI, LLM guardrails", ["exp-idsu-01"]),
      adjacent: s(7, "fleet API team", ["exp-fm-02"]),
      experience: s(7, "Graduate programme", []),
      eligibility: s(9, "London, hybrid", []),
      reason: "your guardrail gateway maps straight onto the assistant team.",
      jobQuote: "Python, FastAPI, LLM guardrails",
      cvBulletIds: ["exp-idsu-01"],
      keywords: kw("Python|FastAPI|LLMs|Guardrails", "Agents|PostgreSQL"),
      model: MODEL,
    },
    questions: GREENHOUSE_QUESTIONS("Oakline Robotics", "the United Kingdom"),
  },
  {
    id: "job-halden-agents",
    title: "Software Engineer, Agents",
    company: "Halden Studio",
    city: "Amsterdam",
    country: "Netherlands",
    remote: "remote",
    salary: { min: 55000, max: 62000, currency: "EUR", period: "year" },
    postedDaysAgo: 0.3,
    seniority: "junior",
    description: `## Halden Studio
Halden Studio makes design-review software for architecture firms.

## The role
Build the agents that check drawings against client briefs, in Python with LangGraph.

## You have
- Python
- LangGraph or another agent framework
- TypeScript for the review UI is a plus`,
    ats: { vendor: "ashby", boardToken: "halden", jobId: "e44c21" },
    applyUrl: "https://jobs.ashbyhq.com/halden/e44c21",
    sources: [{ channel: "board_search", detail: "welcometothejungle.com" }],
    evidence: {
      core: s(6.8, "Python with LangGraph", ["exp-fm-03"]),
      adjacent: s(7, "TypeScript for the review UI", ["exp-fm-01"]),
      experience: s(6),
      eligibility: s(6, "Remote (Netherlands)"),
      reason: "your LangGraph summary agent is direct evidence.",
      jobQuote: "Build the agents that check drawings against client briefs",
      cvBulletIds: ["exp-fm-03"],
      keywords: kw("Python|LangGraph|Agents", "TypeScript"),
      model: MODEL,
    },
  },
];

/** Roles approved on earlier days: packs waiting at Gate 2 and applications in the tracker. */
export interface DemoApplicationSeed {
  job: DemoJobSeed;
  status: "ready_for_review" | "approved" | "submitted" | "interview" | "rejected";
  daysAgo: number;
  submittedDaysAgo?: number;
  followUpInDays?: number;
  notes?: string;
}

export const applicationSeeds: DemoApplicationSeed[] = [
  {
    status: "ready_for_review",
    daysAgo: 0.02,
    job: {
      id: "job-saltmarsh-gaie",
      title: "Graduate AI Engineer",
      company: "Saltmarsh Analytics",
      city: "London",
      country: "United Kingdom",
      remote: "hybrid",
      salary: { min: 44000, max: 50000, currency: "GBP", period: "year" },
      postedDaysAgo: 9,
      seniority: "graduate",
      description: `## Saltmarsh Analytics
Saltmarsh Analytics helps local councils answer residents' questions with a reviewed LLM assistant.

## The role
Build the Python and FastAPI services behind the assistant, including PII redaction and guardrails, and the TypeScript console council staff use to review answers.

## You have
- Python and FastAPI
- LLM APIs, prompting and agents
- TypeScript and PostgreSQL
- Docker is a plus`,
      ats: { vendor: "greenhouse", boardToken: "saltmarsh", jobId: "8812004" },
      applyUrl: "https://job-boards.greenhouse.io/saltmarsh/jobs/8812004",
      sources: [{ channel: "board_search", detail: "gradcracker.com" }],
      evidence: {
        core: s(8.2, "Python and FastAPI services", ["exp-idsu-01"]),
        adjacent: s(8, "TypeScript console", ["exp-fm-01"]),
        experience: s(7),
        eligibility: s(9),
        reason: "your PII gateway is the same problem in a different sector.",
        jobQuote: "including PII redaction and guardrails",
        cvBulletIds: ["exp-idsu-01"],
        keywords: kw("Python|FastAPI|LLMs|PII|Guardrails", "Prompting|Agents|TypeScript|PostgreSQL|Docker"),
        model: MODEL,
      },
      questions: GREENHOUSE_QUESTIONS("Saltmarsh Analytics", "the United Kingdom"),
    },
  },
  {
    status: "ready_for_review",
    daysAgo: 0.1,
    job: {
      id: "job-veridian-aai",
      title: "Software Engineer, Applied AI",
      company: "Veridian Health",
      city: "Dublin",
      country: "Ireland",
      remote: "hybrid",
      salary: { min: 52000, max: 60000, currency: "EUR", period: "year" },
      postedDaysAgo: 10,
      seniority: "junior",
      description: `## Veridian Health
Veridian Health builds scheduling and triage software used by 120 clinics in Ireland.

## The role
Ship applied-AI features: agent workflows for triage notes, REST APIs for clinic systems, and evaluation of model output before release.

## You have
- Python or TypeScript
- REST APIs
- Experience with LLMs or agents
- Model evaluation is a plus`,
      ats: { vendor: "ashby", boardToken: "veridian", jobId: "d81e30" },
      applyUrl: "https://jobs.ashbyhq.com/veridian/d81e30",
      sources: [{ channel: "email_alert", detail: "indeed-alert" }],
      evidence: {
        core: s(7.4, "Python or TypeScript", ["exp-fm-01"]),
        adjacent: s(7),
        experience: s(7),
        eligibility: s(8),
        reason: "agent workflows and REST APIs match; clinical domain is new.",
        jobQuote: "agent workflows for triage notes",
        cvBulletIds: ["exp-idsu-02", "exp-fm-03"],
        keywords: kw("Python|REST APIs|LLMs|Agents", "TypeScript|Model evaluation|LangGraph"),
        model: MODEL,
      },
    },
  },
  {
    status: "approved",
    daysAgo: 1,
    job: trackerJob("job-fenwick-jbe", "Junior Backend Engineer", "Fenwick Rail", "London", "United Kingdom", 6),
  },
  {
    status: "submitted",
    daysAgo: 4,
    submittedDaysAgo: 3,
    followUpInDays: 4,
    job: trackerJob("job-arden-gse", "Graduate Software Engineer", "Arden Maps", "London", "United Kingdom", 8),
  },
  {
    status: "submitted",
    daysAgo: 10,
    submittedDaysAgo: 9,
    followUpInDays: -2,
    job: trackerJob("job-copperleaf-aie", "AI Engineer", "Copperleaf", "Amsterdam", "Netherlands", 14),
  },
  {
    status: "interview",
    daysAgo: 16,
    submittedDaysAgo: 15,
    notes: "Technical screen Thursday 22 October, 14:00. Prepare the gateway design story.",
    job: trackerJob("job-lattice-mle", "ML Engineer, New Grad", "Lattice Works", "Dublin", "Ireland", 20),
  },
  {
    status: "rejected",
    daysAgo: 30,
    submittedDaysAgo: 28,
    notes: "Rejected after the take-home. Feedback: wanted more testing.",
    // Same company and title as a role on today's shortlist, so approving it shows TRK-03.
    job: trackerJob("job-lumen-be-old", "Backend Engineer (Python)", "Lumen Payments", "Dublin", "Ireland", 34),
  },
];

function trackerJob(
  id: string,
  title: string,
  company: string,
  city: string,
  country: string,
  postedDaysAgo: number,
): DemoJobSeed {
  return {
    id,
    title,
    company,
    city,
    country,
    remote: "hybrid",
    postedDaysAgo,
    seniority: "graduate",
    description: `## ${company}
${company} is hiring a ${title.toLowerCase()} to build Python and TypeScript services.

## You have
- Python and REST APIs
- TypeScript and PostgreSQL
- Interest in LLMs`,
    ats: { vendor: "lever", boardToken: company.toLowerCase().replace(/\W+/g, ""), jobId: id.slice(4) },
    applyUrl: `https://jobs.lever.co/${company.toLowerCase().replace(/\W+/g, "")}/${id.slice(4)}`,
    sources: [{ channel: "board_search", detail: "gradcracker.com" }],
    evidence: {
      core: s(7, "Python and REST APIs", ["exp-idsu-01"]),
      adjacent: s(7),
      experience: s(7),
      eligibility: s(8),
      reason: "Python services and REST APIs match.",
      jobQuote: "Python and TypeScript services",
      cvBulletIds: ["exp-idsu-01", "exp-fm-02"],
      keywords: kw("Python|REST APIs|TypeScript", "PostgreSQL|LLMs"),
      model: MODEL,
    },
  };
}
