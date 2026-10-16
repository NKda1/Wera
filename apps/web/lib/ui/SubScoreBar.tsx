export function SubScoreBar({ label, score }: { label: string; score: number }) {
  const shown = Math.round(score);
  return (
    <div className="grid grid-cols-[88px_1fr_20px] items-center gap-3 text-small">
      <span className="text-ink-2">{label}</span>
      <span className="h-1.5 overflow-hidden rounded-full bg-mint-100" aria-hidden="true">
        <span className="block h-full rounded-full bg-forest-700" style={{ width: `${Math.max(0, Math.min(10, score)) * 10}%` }} />
      </span>
      <span className="text-right font-semibold tabular-nums" aria-label={`${label} ${shown} out of 10`}>
        {shown}
      </span>
    </div>
  );
}
