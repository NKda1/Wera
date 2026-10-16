"use client";

import { Download, Lock, Plus, Trash2, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import YAML from "yaml";
import { api, ApiRequestError } from "@/lib/api";
import type { Contact, MasterCV, Role, SkillLevel } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card, CardHeader } from "@/lib/ui/Card";
import { Chip } from "@/lib/ui/Chip";
import { ChipInput } from "@/lib/ui/ChipInput";
import { Dialog } from "@/lib/ui/Dialog";
import { Checkbox, FieldError, Help, Input, Label, Select, Textarea } from "@/lib/ui/Field";
import { Pill } from "@/lib/ui/Pill";
import { useToast } from "@/lib/ui/Toast";

type Kind = "experience" | "projects";

const pad = (n: number) => String(n).padStart(2, "0");

/** CV-04: ids are assigned once and never change when text is edited. */
function nextBulletId(role: Role): string {
  const max = role.bullets.reduce((m, b) => {
    const n = Number(b.id.slice(role.id.length + 1));
    return b.id.startsWith(`${role.id}-`) && Number.isFinite(n) ? Math.max(m, n) : m;
  }, 0);
  return `${role.id}-${pad(max + 1)}`;
}

function nextRoleId(cv: MasterCV, kind: Kind): string {
  const prefix = kind === "experience" ? "exp" : "prj";
  const taken = new Set([...cv.experience, ...cv.projects].map((r) => r.id));
  let n = (kind === "experience" ? cv.experience : cv.projects).length + 1;
  while (taken.has(`${prefix}-${n}`)) n++;
  return `${prefix}-${n}`;
}

export function CvEditor({ initialCv, initialContact }: { initialCv: MasterCV; initialContact: Contact }) {
  const router = useRouter();
  const toast = useToast();
  const [saved, setSaved] = useState(initialCv);
  const [savedContact, setSavedContact] = useState(initialContact);
  const [cv, setCv] = useState(initialCv);
  const [contact, setContact] = useState(initialContact);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState(false);

  const cvDirty = JSON.stringify(cv) !== JSON.stringify(saved);
  const contactDirty = JSON.stringify(contact) !== JSON.stringify(savedContact);
  const dirty = cvDirty || contactDirty;

  const edit = (fn: (d: MasterCV) => void) =>
    setCv((d) => {
      const n = structuredClone(d);
      fn(n);
      return n;
    });
  const err = (path: string) => errors[path];

  const roles = [...cv.experience, ...cv.projects];
  const untagged = roles.flatMap((r) => r.bullets.filter((b) => b.tags.length === 0).map((b) => b.id));

  const save = async () => {
    setSaving(true);
    setErrors({});
    try {
      if (cvDirty) {
        const { version } = await api.putCv(cv);
        const next = { ...cv, version };
        setCv(next);
        setSaved(next);
      }
      if (contactDirty) {
        const c = await api.putContact(contact);
        setContact(c);
        setSavedContact(c);
      }
      setImported(false);
      toast({ tone: "ok", message: cvDirty ? `Saved as version ${saved.version + 1}.` : "Contact details saved." });
      router.refresh();
    } catch (e) {
      if (e instanceof ApiRequestError && e.fields) setErrors(e.fields);
      toast({ tone: "error", message: e instanceof Error ? e.message : "Couldn't save." });
    } finally {
      setSaving(false);
    }
  };

  const exportYaml = () => {
    const blob = new Blob([YAML.stringify(saved)], { type: "application/yaml" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `master-cv-v${saved.version}.yaml`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="mx-auto max-w-[860px]">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Pill tone="green" dot={false}>
            Version {saved.version}
          </Pill>
          <h1 className="mt-3 text-[34px] leading-[1.05] font-bold tracking-[-0.025em] sm:text-display">Master CV</h1>
          <p className="mt-2 max-w-xl text-small text-ink-2">
            The single source of truth for every search and every pack. Tailored lines must cite a line here, and each line keeps its id forever.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => setImporting(true)}>
            <Upload className="size-4" aria-hidden="true" />
            Import
          </Button>
          <Button variant="secondary" onClick={exportYaml} title={`Exports the saved version (v${saved.version}) without contact details`}>
            <Download className="size-4" aria-hidden="true" />
            Export YAML
          </Button>
        </div>
      </header>

      {imported ? (
        <p className="mt-5 rounded-control bg-mint-100 px-4 py-3 text-[14px] text-forest-900" role="status">
          Imported. Nothing is saved yet: check every section, then save it as version {saved.version + 1}.
        </p>
      ) : null}

      {untagged.length || cv.skills.length === 0 ? (
        <div className="mt-5 rounded-control border border-amber-100 bg-amber-50 px-4 py-3 text-[14px] text-amber-700" role="status">
          <p className="font-semibold">Matching works better with a little more detail</p>
          <ul className="mt-1 list-disc pl-5">
            {untagged.length ? (
              <li>
                {untagged.length === 1 ? "1 line has" : `${untagged.length} lines have`} no tags: {untagged.join(", ")}.
              </li>
            ) : null}
            {cv.skills.length === 0 ? <li>Your CV has no skills list, so keyword coverage can&apos;t be checked.</li> : null}
          </ul>
        </div>
      ) : null}

      <div className="mt-6 flex flex-col gap-4">
        <Card className="p-5 sm:p-6" aria-labelledby="you-title">
          <CardHeader id="you-title" title="You" />
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="cv-name">Name</Label>
              <Input id="cv-name" value={cv.person.name} onChange={(e) => edit((d) => void (d.person.name = e.target.value))} invalid={!!err("person.name")} />
              <FieldError>{err("person.name")}</FieldError>
            </div>
            <div>
              <Label htmlFor="cv-location">Location</Label>
              <Input id="cv-location" value={cv.person.location} onChange={(e) => edit((d) => void (d.person.location = e.target.value))} />
            </div>
            <div className="sm:col-span-2">
              <Label htmlFor="cv-headline">Headline</Label>
              <Input id="cv-headline" value={cv.person.headline ?? ""} onChange={(e) => edit((d) => void (d.person.headline = e.target.value))} />
            </div>
          </div>
          <fieldset className="mt-4">
            <legend className="mb-1.5 text-[14px] font-semibold">Links</legend>
            <ul className="space-y-2">
              {cv.person.links.map((l, i) => (
                <li key={i} className="grid grid-cols-[minmax(0,140px)_1fr_auto] gap-2">
                  <Input aria-label={`Link ${i + 1} label`} value={l.label} onChange={(e) => edit((d) => void (d.person.links[i].label = e.target.value))} placeholder="Portfolio" />
                  <Input aria-label={`Link ${i + 1} URL`} value={l.url} onChange={(e) => edit((d) => void (d.person.links[i].url = e.target.value))} placeholder="https://" className="font-mono text-[13px]" />
                  <IconButton label={`Remove link ${l.label || i + 1}`} onClick={() => edit((d) => void d.person.links.splice(i, 1))} />
                </li>
              ))}
            </ul>
            <AddButton onClick={() => edit((d) => void d.person.links.push({ label: "", url: "" }))}>Add link</AddButton>
          </fieldset>
        </Card>

        <Card className="p-5 sm:p-6" aria-labelledby="contact-title">
          <CardHeader
            id="contact-title"
            title="Contact details"
            description="Stored separately and never sent to a model. They're added to your CV and letter on your machine when you export."
            action={<Lock className="size-5 text-forest-700" aria-hidden="true" />}
          />
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="c-email">Email</Label>
              <Input id="c-email" type="email" autoComplete="email" value={contact.email} onChange={(e) => setContact({ ...contact, email: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="c-phone">Phone</Label>
              <Input id="c-phone" type="tel" autoComplete="tel" value={contact.phone ?? ""} onChange={(e) => setContact({ ...contact, phone: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="c-address">Address</Label>
              <Input id="c-address" autoComplete="street-address" value={contact.address ?? ""} onChange={(e) => setContact({ ...contact, address: e.target.value })} />
            </div>
          </div>
        </Card>

        <Card className="p-5 sm:p-6" aria-labelledby="summary-title">
          <CardHeader id="summary-title" title="Summary" action={cv.summary ? <Chip variant="source">{cv.summary.id}</Chip> : null} />
          <Textarea
            aria-labelledby="summary-title"
            className="mt-4"
            rows={3}
            value={cv.summary?.text ?? ""}
            onChange={(e) => edit((d) => void (d.summary = { id: d.summary?.id ?? "sum-01", text: e.target.value }))}
          />
        </Card>

        <RolesCard kind="experience" cv={cv} edit={edit} err={err} />
        <RolesCard kind="projects" cv={cv} edit={edit} err={err} />

        <Card className="p-5 sm:p-6" aria-labelledby="edu-title">
          <CardHeader id="edu-title" title="Education" />
          <ul className="mt-4 space-y-4">
            {cv.education.map((ed, i) => (
              <li key={ed.id} className="grid gap-3 sm:grid-cols-[1fr_1fr_110px_110px_auto] sm:items-end">
                <div>
                  <Label htmlFor={`ed-${i}-degree`}>Degree</Label>
                  <Input id={`ed-${i}-degree`} value={ed.degree} onChange={(e) => edit((d) => void (d.education[i].degree = e.target.value))} invalid={!!err(`education.${i}.degree`)} />
                </div>
                <div>
                  <Label htmlFor={`ed-${i}-inst`}>Institution</Label>
                  <Input id={`ed-${i}-inst`} value={ed.institution} onChange={(e) => edit((d) => void (d.education[i].institution = e.target.value))} invalid={!!err(`education.${i}.institution`)} />
                </div>
                <MonthField id={`ed-${i}-start`} label="Start" value={ed.start ?? ""} onChange={(v) => edit((d) => void (d.education[i].start = (v || undefined) as MasterCV["education"][number]["start"]))} />
                <MonthField id={`ed-${i}-end`} label="End" value={ed.end ?? ""} onChange={(v) => edit((d) => void (d.education[i].end = (v || undefined) as MasterCV["education"][number]["end"]))} />
                <IconButton label={`Remove ${ed.degree || "education"}`} onClick={() => edit((d) => void d.education.splice(i, 1))} />
              </li>
            ))}
          </ul>
          <AddButton onClick={() => edit((d) => void d.education.push({ id: `edu-${pad(d.education.length + 1)}`, degree: "", institution: "" }))}>Add education</AddButton>
        </Card>

        <Card className="p-5 sm:p-6" aria-labelledby="skills-title">
          <CardHeader id="skills-title" title="Skills" description="Tailored CVs can only list skills from here. Aliases help matching (Postgres = PostgreSQL)." />
          <ul className="mt-4 space-y-2">
            {cv.skills.map((s, i) => (
              <li key={i} className="grid grid-cols-[1fr_auto] gap-2 sm:grid-cols-[1fr_1fr_150px_auto]">
                <Input aria-label={`Skill ${i + 1}`} value={s.name} onChange={(e) => edit((d) => void (d.skills[i].name = e.target.value))} />
                <Input
                  aria-label={`${s.name || "Skill"} aliases`}
                  className="col-span-2 sm:col-span-1"
                  placeholder="Aliases, comma separated"
                  value={(s.aliases ?? []).join(", ")}
                  onChange={(e) => edit((d) => void (d.skills[i].aliases = e.target.value.split(",").map((x) => x.trim()).filter(Boolean)))}
                />
                <Select aria-label={`${s.name || "Skill"} level`} value={s.level ?? ""} onChange={(e) => edit((d) => void (d.skills[i].level = (e.target.value || undefined) as SkillLevel | undefined))}>
                  <option value="">No level</option>
                  <option value="familiar">Familiar</option>
                  <option value="proficient">Proficient</option>
                  <option value="expert">Expert</option>
                </Select>
                <IconButton label={`Remove ${s.name || "skill"}`} onClick={() => edit((d) => void d.skills.splice(i, 1))} />
              </li>
            ))}
          </ul>
          <AddButton onClick={() => edit((d) => void d.skills.push({ name: "" }))}>Add skill</AddButton>
        </Card>

        <Card className="p-5 sm:p-6" aria-labelledby="lang-title">
          <CardHeader id="lang-title" title="Languages" />
          <ul className="mt-4 space-y-2">
            {cv.languages.map((l, i) => (
              <li key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                <Input aria-label={`Language ${i + 1}`} value={l.name} onChange={(e) => edit((d) => void (d.languages[i].name = e.target.value))} />
                <Input aria-label={`${l.name || "Language"} level`} value={l.level} onChange={(e) => edit((d) => void (d.languages[i].level = e.target.value))} placeholder="Native, fluent…" />
                <IconButton label={`Remove ${l.name || "language"}`} onClick={() => edit((d) => void d.languages.splice(i, 1))} />
              </li>
            ))}
          </ul>
          <AddButton onClick={() => edit((d) => void d.languages.push({ name: "", level: "" }))}>Add language</AddButton>
        </Card>
      </div>

      <div className="sticky bottom-0 z-10 -mx-4 mt-6 flex items-center justify-end gap-3 border-t border-line bg-mint-50/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-card sm:border sm:bg-white/95 sm:px-4">
        <span className="mr-auto text-small text-ink-2">{dirty ? (cvDirty ? `Unsaved changes · saves as version ${saved.version + 1}` : "Unsaved contact details") : "All changes saved"}</span>
        {dirty ? (
          <Button
            variant="ghost"
            onClick={() => {
              setCv(saved);
              setContact(savedContact);
              setErrors({});
              setImported(false);
            }}
          >
            Discard
          </Button>
        ) : null}
        <Button onClick={save} busy={saving} disabled={!dirty}>
          Save
        </Button>
      </div>

      {importing ? (
        <ImportCvDialog
          onClose={() => setImporting(false)}
          onImported={(proposed) => {
            setCv(proposed);
            setImported(true);
            setImporting(false);
          }}
        />
      ) : null}
    </div>
  );
}

function RolesCard({ kind, cv, edit, err }: { kind: Kind; cv: MasterCV; edit: (fn: (d: MasterCV) => void) => void; err: (p: string) => string | undefined }) {
  const list = cv[kind];
  const title = kind === "experience" ? "Experience" : "Projects";
  return (
    <Card className="p-5 sm:p-6" aria-labelledby={`${kind}-title`}>
      <CardHeader id={`${kind}-title`} title={title} description={kind === "experience" ? "Roles, newest first." : "Side projects, open source, coursework worth showing."} />
      <ul className="mt-4 space-y-5">
        {list.map((role, ri) => {
          const path = `${kind}.${ri}`;
          const present = role.end === "present";
          return (
            <li key={role.id} className="rounded-[12px] border border-line p-4">
              <div className="mb-3 flex items-center justify-between gap-2">
                <Chip variant="source">{role.id}</Chip>
                <IconButton label={`Remove ${role.title || "role"}`} onClick={() => edit((d) => void d[kind].splice(ri, 1))} />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor={`${path}-title`}>{kind === "experience" ? "Title" : "Your role"}</Label>
                  <Input id={`${path}-title`} value={role.title} onChange={(e) => edit((d) => void (d[kind][ri].title = e.target.value))} invalid={!!err(`${path}.title`)} />
                  <FieldError>{err(`${path}.title`)}</FieldError>
                </div>
                <div>
                  <Label htmlFor={`${path}-org`}>{kind === "experience" ? "Organisation" : "Project"}</Label>
                  <Input id={`${path}-org`} value={role.org} onChange={(e) => edit((d) => void (d[kind][ri].org = e.target.value))} invalid={!!err(`${path}.org`)} />
                  <FieldError>{err(`${path}.org`)}</FieldError>
                </div>
                <div>
                  <Label htmlFor={`${path}-loc`}>Location</Label>
                  <Input id={`${path}-loc`} value={role.location ?? ""} onChange={(e) => edit((d) => void (d[kind][ri].location = e.target.value || undefined))} />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <MonthField id={`${path}-start`} label="Start" value={role.start} invalid={!!err(`${path}.start`)} onChange={(v) => edit((d) => void (d[kind][ri].start = v as Role["start"]))} />
                  <div>
                    <MonthField id={`${path}-end`} label="End" value={present ? "" : role.end} disabled={present} invalid={!!err(`${path}.end`)} onChange={(v) => edit((d) => void (d[kind][ri].end = v as Role["end"]))} />
                    <Checkbox
                      className="mt-1"
                      label={<span className="text-small font-normal">Present</span>}
                      checked={present}
                      onChange={(e) => edit((d) => void (d[kind][ri].end = (e.target.checked ? "present" : "") as Role["end"]))}
                    />
                  </div>
                </div>
              </div>

              <p className="mt-4 mb-2 text-[14px] font-semibold">Lines</p>
              <ul className="space-y-3">
                {role.bullets.map((b, bi) => (
                  <li key={b.id} className="rounded-control bg-mint-50 p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <Chip variant="source">{b.id}</Chip>
                      <IconButton label={`Remove line ${b.id}`} onClick={() => edit((d) => void d[kind][ri].bullets.splice(bi, 1))} />
                    </div>
                    <Textarea aria-label={`Line ${b.id}`} rows={2} className="min-h-0" value={b.text} onChange={(e) => edit((d) => void (d[kind][ri].bullets[bi].text = e.target.value))} />
                    <div className="mt-2">
                      <ChipInput label={`Tags for ${b.id}`} hideLabel compact value={b.tags} onChange={(tags) => edit((d) => void (d[kind][ri].bullets[bi].tags = tags))} placeholder="Add tags: python, fastapi…" />
                    </div>
                  </li>
                ))}
              </ul>
              <AddButton onClick={() => edit((d) => void d[kind][ri].bullets.push({ id: nextBulletId(d[kind][ri]), text: "", tags: [] }))}>Add line</AddButton>
            </li>
          );
        })}
      </ul>
      <AddButton
        onClick={() =>
          edit((d) => {
            const now = new Date();
            d[kind].push({ id: nextRoleId(d, kind), title: "", org: "", start: `${now.getFullYear()}-${pad(now.getMonth() + 1)}` as Role["start"], end: "present", bullets: [] });
          })
        }
      >
        {kind === "experience" ? "Add role" : "Add project"}
      </AddButton>
    </Card>
  );
}

function MonthField({ id, label, value, onChange, disabled, invalid }: { id: string; label: string; value: string; onChange: (v: string) => void; disabled?: boolean; invalid?: boolean }) {
  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="month" placeholder="YYYY-MM" pattern="\d{4}-\d{2}" value={value} disabled={disabled} invalid={invalid} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function IconButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label} className="inline-flex size-11 shrink-0 items-center justify-center rounded-control text-ink-2 hover:bg-red-50 hover:text-red-700">
      <Trash2 className="size-4" />
    </button>
  );
}

function AddButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="mt-3 inline-flex min-h-11 items-center gap-1.5 rounded-control px-2 text-[14px] font-semibold text-forest-700 hover:bg-mint-100">
      <Plus className="size-4" aria-hidden="true" />
      {children}
    </button>
  );
}

function ImportCvDialog({ onClose, onImported }: { onClose: () => void; onImported: (cv: MasterCV) => void }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  const submit = async () => {
    setBusy(true);
    setError(undefined);
    try {
      onImported(await api.importCv(text));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't read that file.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Import a master CV"
      description="Load a WERA YAML file. You review it before anything is saved; ids in the file are kept as they are."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} busy={busy} disabled={!text.trim()}>
            Load for review
          </Button>
        </>
      }
    >
      <input
        ref={file}
        type="file"
        accept=".yaml,.yml,application/yaml,text/yaml"
        className="sr-only"
        aria-label="Choose a YAML file"
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (f) setText(await f.text());
        }}
      />
      <Button variant="secondary" onClick={() => file.current?.click()}>
        <Upload className="size-4" aria-hidden="true" />
        Choose a .yaml file
      </Button>
      <div className="mt-4">
        <Label htmlFor="cv-yaml">Or paste YAML</Label>
        <Textarea id="cv-yaml" rows={8} className="font-mono text-[13px]" value={text} onChange={(e) => setText(e.target.value)} invalid={!!error} spellCheck={false} />
        <FieldError>{error}</FieldError>
        <Help>Importing from a PDF or pasted text needs the model to structure it, so it&apos;s available once live mode is on.</Help>
      </div>
    </Dialog>
  );
}
