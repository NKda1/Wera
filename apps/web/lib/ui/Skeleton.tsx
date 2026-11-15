import { cn } from "./cn";

/** A placeholder block in the page's own shape, shown while a screen's data streams in. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse bg-mint-100", className)} aria-hidden="true" />;
}

/** A whole screen's stand-in: a title, a line of controls, and `rows` card-shaped blocks. Rendered
 *  by each route's `loading.tsx`, so navigating shows the page's frame at once instead of nothing. */
export function ScreenSkeleton({ rows = 4, label }: { rows?: number; label: string }) {
  return (
    <div className="mx-auto max-w-[1080px]" role="status" aria-label={label}>
      <Skeleton className="h-9 w-64" />
      <Skeleton className="mt-3 h-4 w-96 max-w-full" />
      <Skeleton className="mt-12 h-10 w-full" />
      <div className="mt-6 flex flex-col gap-4">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="border border-line bg-surface p-6">
            <Skeleton className="h-5 w-1/2" />
            <Skeleton className="mt-3 h-4 w-1/3" />
            <Skeleton className="mt-6 h-3 w-full" />
            <Skeleton className="mt-2 h-3 w-5/6" />
          </div>
        ))}
      </div>
      <span className="sr-only">{label}</span>
    </div>
  );
}
