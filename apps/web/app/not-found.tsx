import { ButtonLink } from "@/lib/ui/Button";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-xl py-16">
      <h1 className="text-h1">Not found</h1>
      <p className="mt-2 text-ink-2">That page or pack doesn&apos;t exist. Demo data resets when the server restarts, so older links stop working.</p>
      <ButtonLink href="/jobs" className="mt-6">
        Back to your shortlist
      </ButtonLink>
    </div>
  );
}
