"use client"; // Error boundaries must be Client Components

import { Button } from "@/lib/ui/Button";

/** Shown inside the app shell when a page can't load, most often because the agent isn't running. */
export default function PageError({ retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <div className="mx-auto max-w-[560px] py-10">
      <h1 className="text-h2 font-bold">This page couldn&apos;t load</h1>
      <p className="mt-2 text-[15px] text-ink-2">
        WERA couldn&apos;t get this page&apos;s data from its agent service. If the agent isn&apos;t running, start WERA with{" "}
        <code className="font-mono text-[14px]">pnpm dev</code>, then try again.
      </p>
      <Button className="mt-5" onClick={() => retry()}>
        Try again
      </Button>
    </div>
  );
}
