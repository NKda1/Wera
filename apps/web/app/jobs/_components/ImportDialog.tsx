"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { isLinkedInUrl } from "@/lib/urls";
import { Button } from "@/lib/ui/Button";
import { Dialog } from "@/lib/ui/Dialog";
import { FieldError, Help, Input, Label, Textarea } from "@/lib/ui/Field";
import { Tabs } from "@/lib/ui/Tabs";
import { useToast } from "@/lib/ui/Toast";

type Mode = "link" | "text";

/** ATS-04: Greenhouse, Lever and Ashby links resolve to their APIs; LinkedIn links are never fetched. */
export function ImportDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>("link");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [linkedin, setLinkedin] = useState(false);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(undefined);
    if (mode === "link") {
      if (isLinkedInUrl(url)) {
        // Caught here, so no request is ever made for the link.
        setLinkedin(true);
        setMode("text");
        return;
      }
      if (!url.trim()) return setError("Paste a link to the job post.");
    } else if (text.trim().length < 20) {
      return setError("Paste the full job description.");
    }
    setBusy(true);
    try {
      const job = await api.importJob(mode === "link" ? { url: url.trim() } : { text });
      toast({ tone: "ok", message: `Added ${job.title} at ${job.company} · fit ${job.fit.total.toFixed(1)}` });
      router.refresh();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't import that job.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Paste a job"
      description="Add a role you found yourself. It's scored against your master CV like everything else."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} busy={busy}>
            Import job
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Tabs<Mode>
          label="Import from"
          idPrefix="import"
          value={mode}
          onChange={(m) => {
            setMode(m);
            setError(undefined);
          }}
          tabs={[
            { id: "link", label: "Link" },
            { id: "text", label: "Description" },
          ]}
        />
        <div className="pt-5" role="tabpanel" id={`import-panel-${mode}`} aria-labelledby={`import-tab-${mode}`}>
          {mode === "link" ? (
            <>
              <Label htmlFor="import-url">Job link</Label>
              <Input
                id="import-url"
                type="url"
                inputMode="url"
                placeholder="https://job-boards.greenhouse.io/company/jobs/123"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                invalid={!!error}
                autoFocus
              />
              <Help>Greenhouse, Lever and Ashby links are read from their public APIs. Other pages are fetched once.</Help>
            </>
          ) : (
            <>
              {linkedin ? (
                <p className="mb-4 rounded-control bg-amber-50 px-3.5 py-2.5 text-[14px] text-amber-700" role="status">
                  WERA never opens LinkedIn links. Open the job on LinkedIn, copy the description, and paste it here.
                </p>
              ) : null}
              <Label htmlFor="import-text">Job description</Label>
              <Textarea
                id="import-text"
                rows={9}
                placeholder={"Junior AI Engineer at Example Co\n\nAbout the role…"}
                value={text}
                onChange={(e) => setText(e.target.value)}
                invalid={!!error}
                autoFocus
              />
              <Help>Put the job title on the first line. Text is treated as data: nothing in it can trigger an action.</Help>
            </>
          )}
          <FieldError>{error}</FieldError>
        </div>
      </form>
    </Dialog>
  );
}
