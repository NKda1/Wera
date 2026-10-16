export function LogoMark({ className = "size-9" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 40 40" aria-hidden="true">
      <rect width="40" height="40" rx="10" fill="#146C43" />
      <path d="M10 13.5 14.6 27 20 16.5 25.4 27 30 13.5" fill="none" stroke="#fff" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark />
      <span className="text-[22px] font-bold tracking-[0.02em] text-forest-900">WERA</span>
    </span>
  );
}
