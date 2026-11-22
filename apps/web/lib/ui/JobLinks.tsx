import { ExternalLink } from "lucide-react";
import type { ApplicationJob } from "@/lib/types";
import { cn } from "./cn";

const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/** Where the job lives, at every stage of its pack (17 Nov): the posting itself, named by the site
 *  it is on, and the employer's own careers site when WERA knows it — so the person can open the
 *  post to apply, or check the company, without hunting for either. */
export function JobLinks({ job, className }: { job: Pick<ApplicationJob, "applyUrl" | "careersUrl" | "company"> & { employerNamed?: boolean }; className?: string }) {
  // 22 Nov: a board's post that names no employer has no employer's careers site to offer.
  const careers = job.careersUrl && job.careersUrl !== job.applyUrl && job.employerNamed !== false ? job.careersUrl : undefined;
  if (!job.applyUrl && !careers) return null;
  const link = "inline-flex items-center gap-1 font-semibold text-forest-700 underline-offset-2 hover:underline";
  return (
    <span className={cn("flex flex-wrap gap-x-4 gap-y-1 text-small", className)}>
      {job.applyUrl ? (
        <a href={job.applyUrl} target="_blank" rel="noreferrer noopener" className={link}>
          Job page on {host(job.applyUrl)}
          <ExternalLink className="size-3.5" aria-hidden="true" />
        </a>
      ) : null}
      {careers ? (
        <a href={careers} target="_blank" rel="noreferrer noopener" className={link}>
          {job.company} careers site
          <ExternalLink className="size-3.5" aria-hidden="true" />
        </a>
      ) : null}
    </span>
  );
}
