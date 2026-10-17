"use client";

import { Plus, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiRequestError } from "@/lib/api";
import { formatAgo } from "@/lib/format";
import { DEFAULT_WEIGHTS, normaliseWeights } from "@/lib/scoring";
import type { FitWeights, SecretStatus, SettingsResponse } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card, CardHeader } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import { Dialog } from "@/lib/ui/Dialog";
import { Checkbox, FieldError, Help, Input, Label, Select } from "@/lib/ui/Field";
import { useToast } from "@/lib/ui/Toast";

const MODEL_OPTIONS = {
  fast: ["qwen/qwen3.7-flash", "qwen/qwen3.8-flash"],
  scoring: ["qwen/qwen3.8-27b", "qwen/qwen3.7-plus"],
  writing: ["qwen/qwen3.8-max-0902", "qwen/qwen3.7-plus"],
};

const WEIGHT_LABELS: { key: keyof FitWeights; label: string; hint: string }[] = [
  { key: "core", label: "Core stack", hint: "The job's main tools against your strongest skills" },
  { key: "adjacent", label: "Adjacent", hint: "Related tools and domains" },
  { key: "experience", label: "Experience", hint: "Level and years asked for" },
  { key: "eligibility", label: "Eligibility", hint: "Location, language, right to work" },
  { key: "freshness", label: "Freshness", hint: "From the posting date, computed in code" },
];

type Draft = Pick<SettingsResponse, "redactContact" | "weeklyBudgetUsd" | "cvPages" | "baseUrl" | "models" | "weights"> & { senders: string[] };

const toDraft = (s: SettingsResponse): Draft => ({
  redactContact: s.redactContact,
  weeklyBudgetUsd: s.weeklyBudgetUsd,
  cvPages: s.cvPages,
  baseUrl: s.baseUrl,
  models: { ...s.models },
  weights: { ...s.weights },
  senders: [...s.inbox.senders],
});

export function SettingsScreen({ initial }: { initial: SettingsResponse }) {
  const router = useRouter();
  const toast = useToast();
  const [settings, setSettings] = useState(initial);
  const [draft, setDraft] = useState(() => toDraft(initial));
  const [budgetText, setBudgetText] = useState(initial.weeklyBudgetUsd.toFixed(2));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [confirmRedact, setConfirmRedact] = useState(false);

  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(settings));
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));

  const save = async () => {
    const budget = Number(budgetText);
    if (!Number.isFinite(budget)) return setErrors({ weeklyBudgetUsd: "Enter an amount in US dollars, like 5.00" });
    setSaving(true);
    setErrors({});
    try {
      const { senders, ...rest } = draft;
      const next = await api.putSettings({ ...rest, weeklyBudgetUsd: budget, inbox: { senders } });
      setSettings(next);
      setDraft(toDraft(next));
      setBudgetText(next.weeklyBudgetUsd.toFixed(2));
      toast({ tone: "ok", message: "Settings saved." });
      router.refresh();
    } catch (err) {
      if (err instanceof ApiRequestError && err.fields) setErrors(err.fields);
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't save settings." });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-[760px]">
      <h1 className="text-[34px] leading-[1.05] font-bold tracking-[-0.025em] sm:text-display">Settings</h1>
      <p className="mt-2 text-small text-ink-2">Provider, models and keys for this instance.</p>

      <div className="mt-6 flex flex-col gap-4">
        <KeysCard settings={settings} />
        <InboxCard settings={settings} senders={draft.senders} onSenders={(s) => set("senders", s)} onChecked={(at) => setSettings((s) => ({ ...s, inbox: { ...s.inbox, lastCheckedAt: at } }))} />

        <Card className="p-5 sm:p-6" aria-labelledby="models-title">
          <CardHeader id="models-title" title="Models" description="Any OpenAI-compatible endpoint. Pin dated model ids so results stay stable." />
          <div className="mt-4">
            <Label htmlFor="base-url">Base URL</Label>
            <Input id="base-url" className="font-mono text-mono" value={draft.baseUrl} onChange={(e) => set("baseUrl", e.target.value)} invalid={!!errors.baseUrl} spellCheck={false} />
            <FieldError>{errors.baseUrl}</FieldError>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <ModelSelect id="fast" label="Fast tier" help="Importing, keywords, drafts" value={draft.models.fast} options={MODEL_OPTIONS.fast} onChange={(v) => set("models", { ...draft.models, fast: v })} />
            <ModelSelect id="scoring" label="Scoring tier" help="Fit scores · reasoning low" value={draft.models.scoring} options={MODEL_OPTIONS.scoring} onChange={(v) => set("models", { ...draft.models, scoring: v })} />
            <ModelSelect id="writing" label="Writing tier" help="CV, cover letter, answers" value={draft.models.writing} options={MODEL_OPTIONS.writing} onChange={(v) => set("models", { ...draft.models, writing: v })} />
          </div>
        </Card>

        <WeightsCard weights={draft.weights} onChange={(w) => set("weights", w)} />

        <Card className="p-5 sm:p-6" aria-labelledby="privacy-title">
          <CardHeader id="privacy-title" title="Privacy and spending" />
          <div className="mt-3 space-y-3">
            <Checkbox
              label="Demo mode"
              checked={settings.demoMode}
              disabled
              readOnly
              description={
                <>
                  Use sample jobs and a mock model. No keys needed, nothing is spent.{" "}
                  <span className="text-ink">This build runs on demo data only; live intake arrives with the backend.</span>
                </>
              }
            />
            <Checkbox
              label="Redact contact details before prompts"
              checked={draft.redactContact}
              onChange={(e) => (e.target.checked ? set("redactContact", true) : setConfirmRedact(true))}
              description="Phone, email and address are removed before text leaves your machine and added back afterwards."
            />
          </div>

          <fieldset className="mt-5">
            <legend className="mb-1.5 text-[14px] font-semibold">Tailored CV length</legend>
            <div className="inline-flex rounded-control border border-line bg-mint-50 p-1" role="radiogroup" aria-label="Tailored CV length">
              {([1, 2] as const).map((n) => (
                <button
                  key={n}
                  type="button"
                  role="radio"
                  aria-checked={draft.cvPages === n}
                  onClick={() => set("cvPages", n)}
                  className={cn("min-h-10 rounded-[7px] px-4 text-[14px] font-semibold", draft.cvPages === n ? "bg-white text-forest-900 shadow-card" : "text-ink-2 hover:text-ink")}
                >
                  {n} page{n > 1 ? "s" : ""}
                </button>
              ))}
            </div>
            <Help>Applies to packs drafted from now on.</Help>
          </fieldset>

          <div className="mt-5">
            <Label htmlFor="budget">Weekly model budget (USD)</Label>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <Input
                id="budget"
                inputMode="decimal"
                className="max-w-36 tabular-nums"
                value={budgetText}
                onChange={(e) => {
                  setBudgetText(e.target.value);
                  const n = Number(e.target.value);
                  if (Number.isFinite(n)) set("weeklyBudgetUsd", n);
                }}
                invalid={!!errors.weeklyBudgetUsd}
                aria-describedby="budget-help"
              />
              <BudgetNote id="budget-help" spent={settings.spentThisWeekUsd} budget={settings.weeklyBudgetUsd} />
            </div>
            <FieldError>{errors.weeklyBudgetUsd}</FieldError>
          </div>
        </Card>
      </div>

      <div className="sticky bottom-0 z-10 -mx-4 mt-6 flex items-center justify-end gap-3 border-t border-line bg-mint-50/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-card sm:border sm:bg-white/95 sm:px-4">
        {dirty ? <span className="text-small text-ink-2">Unsaved changes</span> : null}
        {dirty ? (
          <Button
            variant="ghost"
            onClick={() => {
              setDraft(toDraft(settings));
              setBudgetText(settings.weeklyBudgetUsd.toFixed(2));
              setErrors({});
            }}
          >
            Discard
          </Button>
        ) : null}
        <Button onClick={save} busy={saving} disabled={!dirty}>
          Save settings
        </Button>
      </div>

      <Dialog
        open={confirmRedact}
        onClose={() => setConfirmRedact(false)}
        size="sm"
        title="Send contact details to the model?"
        description="With redaction off, your phone, email and address go to the model provider with every prompt. WERA works the same with it on."
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirmRedact(false)}>
              Keep redaction on
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                set("redactContact", false);
                setConfirmRedact(false);
              }}
            >
              Turn it off
            </Button>
          </>
        }
      />
    </div>
  );
}

/** SET-02: one live check per provider. The reply is already sanitised by the agent (§9.4). */
function TestConnection({ provider }: { provider: "llm" | "firecrawl" | "inbox" }) {
  const toast = useToast();
  const [testing, setTesting] = useState(false);
  return (
    <Button
      variant="secondary"
      busy={testing}
      onClick={async () => {
        setTesting(true);
        try {
          const r = await api.testProvider(provider);
          toast({ tone: r.ok ? "ok" : "attention", message: r.message });
        } catch (err) {
          toast({ tone: "error", message: err instanceof Error ? err.message : "Test failed." });
        } finally {
          setTesting(false);
        }
      }}
    >
      Test connection
    </Button>
  );
}

function KeysCard({ settings }: { settings: SettingsResponse }) {
  const missing = Object.values(settings.secrets).some((s) => s !== "set");
  return (
    <Card className="p-5 sm:p-6" aria-labelledby="keys-title">
      <CardHeader
        id="keys-title"
        title="API keys"
        description="Keys live only in your server's environment. WERA shows whether a key is set — never its value — and never sends it to the browser or writes it to logs."
      />
      <ul className="mt-4 space-y-3">
        <KeyRow
          name="LLM_API_KEY"
          status={settings.secrets.llm}
          detail={
            settings.secrets.llm === "set"
              ? `Set on server · ${settings.llmProvider}`
              : "Not set · scoring and writing use the mock model in demo mode."
          }
          action={settings.secrets.llm === "set" ? <TestConnection provider="llm" /> : undefined}
        />
        <KeyRow
          name="FIRECRAWL_API_KEY"
          status={settings.secrets.firecrawl}
          detail={settings.secrets.firecrawl === "set" ? "Set on server · job-board discovery is on." : "Not set · job-board discovery is off. Greenhouse, Lever and Ashby imports still work."}
          action={settings.secrets.firecrawl === "set" ? <TestConnection provider="firecrawl" /> : undefined}
        />
        <KeyRow
          name="ALERTS_IMAP_APP_PASSWORD"
          status={settings.secrets.inbox}
          detail={
            settings.secrets.inbox === "set"
              ? `Set on server · read-only access to ${settings.inbox.folder}.`
              : "Not set · the alert inbox channel is off."
          }
          action={settings.secrets.inbox === "set" ? <TestConnection provider="inbox" /> : undefined}
        />
      </ul>
      {missing ? (
        <pre className="mt-4 overflow-x-auto rounded-control bg-code px-4 py-3.5 font-mono text-[13px] leading-relaxed text-white">
          <span className="text-white/60"># add to .env.local on the server, then restart</span>
          {"\n"}
          {settings.secrets.llm !== "set" ? "LLM_API_KEY=…\n" : ""}
          {settings.secrets.firecrawl !== "set" ? "FIRECRAWL_API_KEY=…\n" : ""}
          {settings.secrets.inbox !== "set" ? "ALERTS_IMAP_APP_PASSWORD=…" : ""}
        </pre>
      ) : null}
    </Card>
  );
}

function KeyRow({ name, status, detail, action }: { name: string; status: SecretStatus; detail: string; action?: React.ReactNode }) {
  const ok = status === "set";
  return (
    <li className={cn("flex flex-wrap items-center gap-3 rounded-[12px] border px-4 py-3", ok ? "border-line bg-white" : "border-amber-100 bg-amber-50/60")}>
      <span className={cn("size-2 shrink-0 rounded-full", ok ? "bg-forest-700" : "bg-amber-500")} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="font-mono text-mono font-semibold">{name}</p>
        <p className="text-small text-ink-2">{detail}</p>
      </div>
      <span className={cn("rounded-full px-2.5 py-1 text-[12px] font-semibold", ok ? "bg-mint-100 text-forest-900" : "bg-amber-50 text-amber-700")}>
        {ok ? "Set" : status === "invalid" ? "Invalid" : "Needs key"}
      </span>
      {action}
    </li>
  );
}

function InboxCard({
  settings,
  senders,
  onSenders,
  onChecked,
}: {
  settings: SettingsResponse;
  senders: string[];
  onSenders: (s: string[]) => void;
  onChecked: (at: string) => void;
}) {
  const toast = useToast();
  const [checking, setChecking] = useState(false);
  const [adding, setAdding] = useState(false);
  const [newSender, setNewSender] = useState("");
  const addRef = useRef<HTMLInputElement>(null);
  const [, tick] = useState(0);

  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (adding) addRef.current?.focus();
  }, [adding]);

  const checkNow = async () => {
    setChecking(true);
    try {
      const { runIds } = await api.startRun(["email_alert"]);
      let run = await api.getRun(runIds[0]);
      while (run.status === "running") {
        await new Promise((r) => setTimeout(r, 600));
        run = await api.getRun(runIds[0]);
      }
      onChecked(run.finishedAt ?? new Date().toISOString());
      toast({ tone: "ok", message: run.newJobs ? `${run.newJobs} new jobs from alert emails.` : "Inbox checked. No new alert emails." });
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't check the inbox." });
    } finally {
      setChecking(false);
    }
  };

  const commit = () => {
    const v = newSender.trim();
    if (v && !senders.includes(v)) onSenders([...senders, v]);
    setNewSender("");
    setAdding(false);
  };

  return (
    <Card className="p-5 sm:p-6" aria-labelledby="inbox-title">
      <CardHeader
        id="inbox-title"
        title="Job-alert inbox"
        description="Point your LinkedIn, Indeed and job-board alerts at a separate mailbox. WERA reads one folder of it, read-only, and turns each alert into job cards."
      />
      <div className="mt-4 flex flex-wrap items-center gap-3 rounded-[12px] border border-line px-4 py-3">
        <span className="size-2 shrink-0 rounded-full bg-forest-700" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-mono text-mono font-semibold break-all">
            {settings.inbox.label} · folder {settings.inbox.folder}
          </p>
          <p className="text-small text-ink-2">
            IMAP · checked {formatAgo(settings.inbox.lastCheckedAt)} · {settings.inbox.emailsThisWeek} alert emails this week
          </p>
        </div>
        <span className="rounded-full bg-mint-100 px-2.5 py-1 text-[12px] font-semibold text-forest-900">Read-only</span>
        <Button variant="secondary" onClick={checkNow} busy={checking}>
          Check now
        </Button>
      </div>

      <p className="mt-5 mb-2 text-[14px] font-semibold">Allowed senders</p>
      <div className="flex flex-wrap items-center gap-2">
        {senders.map((s) => (
          <span key={s} className="inline-flex items-center gap-0.5 rounded-full bg-mint-100 py-0.5 pr-1 pl-3 text-small">
            {s}
            <button type="button" onClick={() => onSenders(senders.filter((x) => x !== s))} className="inline-flex size-7 items-center justify-center rounded-full hover:bg-mint-200" aria-label={`Remove ${s}`}>
              <X className="size-3.5" />
            </button>
          </span>
        ))}
        {adding ? (
          <input
            ref={addRef}
            value={newSender}
            onChange={(e) => setNewSender(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") setAdding(false);
            }}
            onBlur={commit}
            placeholder="Sender name or domain"
            aria-label="New sender"
            className="min-h-9 rounded-full border border-forest-700 px-3 text-small focus-visible:outline-none"
          />
        ) : (
          <button type="button" onClick={() => setAdding(true)} className="inline-flex min-h-9 items-center gap-1 rounded-full border border-dashed border-line px-3 text-small font-semibold text-forest-900 hover:bg-mint-50">
            <Plus className="size-3.5" aria-hidden="true" />
            Add sender
          </button>
        )}
      </div>
      <p className="mt-2 text-small text-ink-2">Emails from anyone else are never opened. Each sender must also pass DKIM.</p>

      <p className="mt-4 rounded-control bg-mint-50 px-4 py-3 text-small leading-relaxed text-ink">
        Email text is treated as data. WERA only extracts job cards: it never follows instructions or opens attachments in an email, never opens LinkedIn links, and discards the message once the jobs are saved.
      </p>
    </Card>
  );
}

function ModelSelect({ id, label, help, value, options, onChange }: { id: string; label: string; help: string; value: string; options: string[]; onChange: (v: string) => void }) {
  const all = options.includes(value) ? options : [value, ...options];
  return (
    <div>
      <Label htmlFor={`model-${id}`}>{label}</Label>
      <Select id={`model-${id}`} value={value} onChange={(e) => onChange(e.target.value)} className="text-[14px]">
        {all.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Select>
      <Help>{help}</Help>
    </div>
  );
}

function WeightsCard({ weights, onChange }: { weights: FitWeights; onChange: (w: FitWeights) => void }) {
  const normalised = useMemo(() => normaliseWeights(weights), [weights]);
  const isDefault = WEIGHT_LABELS.every(({ key }) => Math.abs(weights[key] - DEFAULT_WEIGHTS[key]) < 1e-9);
  return (
    <Card className="scroll-mt-24 p-5 sm:p-6" id="weights" aria-labelledby="weights-title">
      <CardHeader
        id="weights-title"
        title="Scoring weights"
        description="The fit total is weighted in code from the model's sub-scores. Changing a weight re-ranks your shortlist with no new model calls."
        action={
          <Button variant="ghost" disabled={isDefault} onClick={() => onChange({ ...DEFAULT_WEIGHTS })}>
            Reset
          </Button>
        }
      />
      <ul className="mt-4 space-y-3.5">
        {WEIGHT_LABELS.map(({ key, label, hint }) => (
          <li key={key} className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 sm:grid-cols-[180px_1fr_48px]">
            <label htmlFor={`w-${key}`} className="text-[14px] font-semibold">
              {label}
              <span className="block text-small font-normal text-ink-2">{hint}</span>
            </label>
            <input
              id={`w-${key}`}
              type="range"
              min={0}
              max={60}
              step={5}
              value={Math.round(weights[key] * 100)}
              onChange={(e) => onChange({ ...weights, [key]: Number(e.target.value) / 100 })}
              className="col-span-2 h-11 w-full accent-forest-700 sm:col-span-1"
              aria-valuetext={`${Math.round(normalised[key] * 100)}% of the total`}
            />
            <span className="row-start-1 text-right text-[14px] font-semibold tabular-nums sm:row-start-auto">{Math.round(normalised[key] * 100)}%</span>
          </li>
        ))}
      </ul>
      <Help>Shown as a share of the total; weights are normalised to 100%.</Help>
    </Card>
  );
}

function BudgetNote({ id, spent, budget }: { id: string; spent: number; budget: number }) {
  const share = budget > 0 ? spent / budget : 1;
  return (
    <p id={id} className={cn("text-small", share >= 0.8 ? "font-semibold text-amber-700" : "text-ink-2")}>
      {share >= 1
        ? `Budget reached: $${spent.toFixed(2)} used. Searches and drafts are paused until you raise it or the week resets.`
        : share >= 0.8
          ? `$${spent.toFixed(2)} used, over 80% of this week's budget.`
          : `Runs pause when the budget is reached. This week: $${spent.toFixed(2)} used.`}
    </p>
  );
}
