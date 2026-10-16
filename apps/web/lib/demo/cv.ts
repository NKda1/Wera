// Fictional demo persona (DEMO-01). Same shape as a real graduate CV so every screen
// has realistic content, but no real person's details ship in this public repo.

import type { Contact, MasterCV, SearchProfile } from "../types";

export const demoCv: MasterCV = {
  version: 4,
  person: {
    name: "Jordan Ellery",
    headline: "Computer Science graduate building production AI tooling",
    location: "London",
    links: [
      { label: "Portfolio", url: "https://jordan-ellery.example" },
      { label: "Code", url: "https://code.jordan-ellery.example" },
    ],
  },
  summary: {
    id: "sum-01",
    text:
      "Computer Science graduate (2026) building production AI tooling: a FastAPI PII-detection gateway for LLM prompts at the IDSU, and a live TypeScript and Postgres platform with real users.",
  },
  experience: [
    {
      id: "exp-idsu",
      title: "AI Intern",
      org: "International Digital Standards Union",
      location: "Geneva",
      start: "2026-02",
      end: "2026-08",
      bullets: [
        {
          id: "exp-idsu-01",
          text: "Built a FastAPI API gateway that scans prompts for personal data (regex and Presidio) before they reach third-party LLMs.",
          tags: ["python", "fastapi", "pii", "llms", "guardrails", "presidio"],
        },
        {
          id: "exp-idsu-02",
          text: "Orchestrated agentic AI workflows hosted on Azure AI Foundry from C# / .NET for internal workflow tooling.",
          tags: ["agents", "llms", "c#", ".net", "azure"],
        },
        {
          id: "exp-idsu-03",
          text: "Wrote prompt templates and evaluation notes for the gateway's redaction rules with the security team.",
          tags: ["prompting", "llms", "security"],
        },
      ],
    },
    {
      id: "exp-fm",
      title: "Founder and Developer",
      org: "Fieldmark",
      location: "London",
      start: "2024-09",
      end: "present",
      bullets: [
        {
          id: "exp-fm-01",
          text: "Built and run a scouting platform for grassroots basketball clubs in Europe on Next.js, Supabase / Postgres and Stripe, with real users.",
          tags: ["typescript", "next.js", "postgresql", "stripe", "product"],
        },
        {
          id: "exp-fm-02",
          text: "Integrated Stripe, Postmark and a video-hosting API into the product's REST API layer.",
          tags: ["rest-apis", "typescript", "stripe", "integrations"],
        },
        {
          id: "exp-fm-03",
          text: "Added a LangGraph agent in Python that drafts weekly scouting summaries from coach notes for review.",
          tags: ["langgraph", "agents", "llms", "python"],
        },
      ],
    },
  ],
  projects: [
    {
      id: "prj-oss",
      title: "Open-source contributor",
      org: "LangChain community integrations",
      start: "2025-03",
      end: "2025-06",
      bullets: [
        {
          id: "prj-oss-01",
          text: "Added structured-output retries with schema validation to a LangChain community model integration in Python.",
          tags: ["python", "langchain", "llms", "open-source"],
        },
      ],
    },
  ],
  education: [
    {
      id: "edu-01",
      degree: "BSc Computer Science",
      institution: "University of Westmoor",
      start: "2023-09",
      end: "2026-06",
    },
  ],
  skills: [
    { name: "Python", level: "proficient" },
    { name: "TypeScript", level: "proficient" },
    { name: "FastAPI", level: "proficient" },
    { name: "Next.js", level: "proficient" },
    { name: "PostgreSQL", aliases: ["Postgres"], level: "proficient" },
    { name: "LangChain", level: "familiar" },
    { name: "LangGraph", level: "familiar" },
    { name: "REST APIs", level: "proficient" },
    { name: "C# / .NET", level: "familiar" },
    { name: "LLM guardrails", aliases: ["Guardrails"], level: "familiar" },
    { name: "Prompt engineering", aliases: ["Prompting"], level: "familiar" },
    { name: "Presidio", level: "familiar" },
    { name: "Stripe", level: "familiar" },
  ],
  languages: [
    { name: "English", level: "Native" },
    { name: "French", level: "Professional working" },
  ],
};

export const demoContact: Contact = {
  email: "jordan.ellery@example.com",
  phone: "+44 7700 900123",
  address: "London",
};

export const demoProfile = (now: Date): SearchProfile => ({
  titles: ["AI Engineer", "Graduate Software Engineer", "ML Engineer", "Backend Engineer (Python)"],
  skills: ["Python", "FastAPI", "TypeScript", "LangGraph", "PostgreSQL", "LLM guardrails"],
  locations: ["London", "Amsterdam", "Dublin", "Berlin", "Zurich"],
  remote: "any",
  seniority: ["graduate", "junior", "entry"],
  exclude: ["Senior", "Staff", "10+ years"],
  sites: ["brightnetwork.co.uk", "gradcracker.com", "welcometothejungle.com", "builtinlondon.uk"],
  recency: "7d",
  channels: { boardSearch: true, inbox: true, watchlist: true },
  generatedFromCvVersion: 4,
  updatedAt: now.toISOString(),
});

/** Boards with their reviewed terms (SRCH-04). Mirrors config/sites.yaml. */
export const SITES: { domain: string; name: string; termsReviewedOn: string | null }[] = [
  { domain: "brightnetwork.co.uk", name: "Bright Network", termsReviewedOn: "2026-10-10" },
  { domain: "gradcracker.com", name: "Gradcracker", termsReviewedOn: "2026-10-10" },
  { domain: "welcometothejungle.com", name: "Welcome to the Jungle", termsReviewedOn: "2026-10-11" },
  { domain: "builtinlondon.uk", name: "Built In London", termsReviewedOn: "2026-10-11" },
  { domain: "otta.com", name: "Otta", termsReviewedOn: null },
];
