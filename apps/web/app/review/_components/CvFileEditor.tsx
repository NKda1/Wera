"use client";

import { RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiRequestError } from "@/lib/api";
import { lineRange, type PageSize, readDesign, withDesign, withPaper } from "@/lib/cvfile";
import type { ApplicationWithJob, CvFile, CvIssue } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { Dialog } from "@/lib/ui/Dialog";
import { Label, Select } from "@/lib/ui/Field";
import { Skeleton } from "@/lib/ui/Skeleton";
import { useToast } from "@/lib/ui/Toast";
import { cn } from "@/lib/ui/cn";

// Long enough that a person typing a word doesn't render every letter; short enough that the
// page follows them. A render is about 120 ms in the agent.
const PREVIEW_DELAY_MS = 600;

/**
 * EXP-01 (18 Nov): the pack's CV as a RenderCV file — the YAML itself, a theme and paper picker,
 * and the pages it renders to. The PDF download is rendered from exactly this file, and the Word
 * file is written from it, so what is previewed here is what is sent.
 */
export function CvFileEditor({ appId, onSaved }: { appId: string; onSaved: (app: ApplicationWithJob) => void }) {
  const toast = useToast();
  const area = useRef<HTMLTextAreaElement>(null);
  const [file, setFile] = useState<CvFile>();
  const [loadError, setLoadError] = useState<string>();
  const [yaml, setYaml] = useState("");
  const [pages, setPages] = useState<string[]>([]);
  const [issues, setIssues] = useState<CvIssue[]>([]);
  const [rendering, setRendering] = useState(false);
  const [busy, setBusy] = useState<"save" | "reset">();
  const [resetting, setResetting] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    let live = true;
    api.getCvFile(appId).then(
      (f) => {
        if (!live) return;
        setFile(f);
        setYaml(f.yaml);
      },
      (e: unknown) => live && setLoadError(e instanceof Error ? e.message : "Couldn't load the CV file."),
    );
    return () => {
      live = false;
    };
  }, [appId]);

  // The preview follows the text, newest request wins. A file that doesn't render keeps the last
  // pages it did render, dimmed, so a half-typed line doesn't blank the page.
  useEffect(() => {
    if (!file) return;
    const mine = ++seq.current;
    setRendering(true);
    const t = setTimeout(async () => {
      try {
        const out = await api.previewCvFile(appId, yaml);
        if (mine !== seq.current) return;
        setIssues(out.issues);
        if (out.pages.length) setPages(out.pages);
      } catch (e) {
        if (mine === seq.current) setIssues([{ message: e instanceof Error ? e.message : "Couldn't render the preview." }]);
      } finally {
        if (mine === seq.current) setRendering(false);
      }
    }, PREVIEW_DELAY_MS);
    return () => clearTimeout(t);
  }, [appId, file, yaml]);

  if (loadError) {
    return (
      <Card className="p-6">
        <p className="text-ink-2">{loadError}</p>
      </Card>
    );
  }
  if (!file) {
    return (
      <Card className="space-y-3 p-6" aria-busy="true">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-64 w-full" />
      </Card>
    );
  }

  const editable = file.editable;
  const dirty = yaml !== file.yaml;
  const theme = readDesign(yaml, "theme") ?? file.theme;
  const paper = (readDesign(yaml, "size") as PageSize | null) ?? file.pageSize;
  const chosen = file.themes.find((t) => t.id === theme);

  const pickTheme = (id: string) => {
    const next = file.themes.find((t) => t.id === id);
    if (next) setYaml((y) => withPaper(withDesign(y, next.design), paper));
  };

  const goTo = (line?: number) => {
    const el = area.current;
    if (!el || !line) return;
    const [start, end] = lineRange(yaml, line);
    el.focus();
    el.setSelectionRange(start, end);
  };

  const save = async (body: { yaml: string } | { reset: true }) => {
    setBusy("reset" in body ? "reset" : "save");
    try {
      const out = await api.saveCvFile(appId, body);
      setFile(out.file);
      setYaml(out.file.yaml);
      onSaved(out.application);
      toast({
        tone: "ok",
        message:
          "reset" in body
            ? "Back to WERA's version. Your design stays."
            : out.file.editedByUser
              ? "Saved. The PDF and Word files use your version of the CV."
              : "Saved. The PDF uses this design.",
      });
      return true;
    } catch (e) {
      if (e instanceof ApiRequestError && e.code === "CV_FILE_INVALID" && e.fields) {
        setIssues(Object.entries(e.fields).map(([where, message]) => ({ message, line: Number(where.match(/^line (\d+)$/)?.[1]) || undefined })));
      }
      toast({ tone: "error", message: e instanceof Error ? e.message : "That didn't save." });
      return false;
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <div className="@container/file space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-48 flex-1">
            <Label htmlFor="cv-theme">CV theme</Label>
            <Select id="cv-theme" value={theme} onChange={(e) => pickTheme(e.target.value)} disabled={!editable}>
              {file.themes.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
              {chosen ? null : <option value={theme}>{theme}</option>}
            </Select>
          </div>
          <div className="w-40">
            <Label htmlFor="cv-paper">Paper</Label>
            <Select id="cv-paper" value={paper} onChange={(e) => setYaml((y) => withPaper(y, e.target.value as PageSize))} disabled={!editable}>
              <option value="a4">A4</option>
              <option value="us-letter">US Letter</option>
            </Select>
          </div>
          {editable ? (
            <div className="flex flex-wrap gap-2">
              {file.editedByUser ? (
                <Button variant="ghost" onClick={() => setResetting(true)}>
                  <RotateCcw className="size-4" aria-hidden="true" />
                  Discard my edits
                </Button>
              ) : null}
              <Button variant="secondary" onClick={() => setYaml(file.yaml)} disabled={!dirty}>
                Undo changes
              </Button>
              <Button onClick={() => save({ yaml })} disabled={!dirty || issues.length > 0} busy={busy === "save"}>
                Save
              </Button>
            </div>
          ) : null}
        </div>
        <p className="mt-2 text-small text-ink-2">{chosen?.note}</p>
        <p className="mt-2 text-small text-ink-2">
          {!editable
            ? "This pack is approved, so its file is fixed: a download next week is the same file."
            : file.editedByUser
              ? "This is your own version of the CV, and the PDF and Word downloads use it. Line editing waits until you discard it."
              : "Built from the tailored CV, so line edits on the Lines view show up here. Rewrite anything below and it becomes your version."}{" "}
          Your email and phone are added when the PDF is made.
        </p>
      </Card>

      <div className="grid items-start gap-4 @3xl/file:grid-cols-2">
        <Card className="p-0">
          <label htmlFor="cv-yaml" className="sr-only">
            CV file (RenderCV YAML)
          </label>
          <textarea
            id="cv-yaml"
            ref={area}
            value={yaml}
            onChange={(e) => setYaml(e.target.value)}
            readOnly={!editable}
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            rows={30}
            aria-describedby={issues.length ? "cv-yaml-issues" : undefined}
            aria-invalid={issues.length > 0 || undefined}
            className="block min-h-[420px] w-full resize-y rounded-card bg-surface p-4 font-mono text-[13px] leading-relaxed text-ink focus-visible:ring-2 focus-visible:ring-forest-700/20 focus-visible:outline-none"
          />
          {issues.length ? (
            <ul id="cv-yaml-issues" className="space-y-1 border-t border-line p-4 text-small text-red-700" role="alert">
              {issues.map((issue, i) => (
                <li key={i}>
                  {issue.line ? (
                    <button type="button" onClick={() => goTo(issue.line)} className="mr-1.5 font-semibold underline">
                      Line {issue.line}
                    </button>
                  ) : null}
                  {issue.message}
                </li>
              ))}
            </ul>
          ) : null}
        </Card>

        <section aria-label="Preview" aria-busy={rendering || undefined} className="space-y-3">
          <p className="text-small text-ink-2" aria-live="polite">
            {rendering ? "Rendering…" : issues.length ? "Fix the file to update the preview." : `${pages.length} page${pages.length === 1 ? "" : "s"}, as the PDF will download.`}
          </p>
          {pages.length ? (
            pages.map((src, i) => (
              // A data: URL of a page RenderCV just drew; next/image has nothing to optimise here.
              <img
                key={i}
                src={src}
                alt={`Page ${i + 1} of the CV`}
                className={cn("w-full border border-line bg-white shadow-sm transition-opacity", (rendering || issues.length > 0) && "opacity-60")}
              />
            ))
          ) : (
            <Skeleton className="aspect-[1/1.414] w-full" />
          )}
        </section>
      </div>

      <Dialog
        open={resetting}
        onClose={() => setResetting(false)}
        size="sm"
        title="Discard your edits to the CV file?"
        description="The file goes back to WERA's tailored CV, with your theme and paper kept. Your own wording is gone, though earlier versions stay in the pack's history."
        footer={
          <>
            <Button variant="secondary" onClick={() => setResetting(false)}>
              Keep my version
            </Button>
            <Button
              variant="danger"
              busy={busy === "reset"}
              onClick={async () => {
                if (await save({ reset: true })) setResetting(false);
              }}
            >
              Discard
            </Button>
          </>
        }
      />
    </div>
  );
}
