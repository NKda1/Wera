import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "./cn";

type Variant = "primary" | "secondary" | "ghost" | "dark" | "danger";

const base =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-control px-5 text-[15px] font-semibold whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50";

const variants: Record<Variant, string> = {
  primary: "bg-forest-700 text-white hover:bg-forest-800 active:bg-forest-900",
  secondary: "border border-line bg-white text-forest-900 hover:border-mint-200 hover:bg-mint-50",
  ghost: "text-ink-2 hover:bg-mint-100 hover:text-ink",
  dark: "bg-forest-900 text-white hover:bg-forest-800",
  danger: "border border-line bg-white text-red-700 hover:bg-red-50",
};

export function buttonClass(variant: Variant = "primary", className?: string) {
  return cn(base, variants[variant], className);
}

export function Button({
  variant = "primary",
  className,
  busy,
  children,
  ...props
}: ComponentProps<"button"> & { variant?: Variant; busy?: boolean }) {
  return (
    <button type="button" className={buttonClass(variant, className)} aria-busy={busy || undefined} {...props}>
      {busy ? <Spinner /> : null}
      {children}
    </button>
  );
}

export function ButtonLink({
  variant = "primary",
  className,
  children,
  ...props
}: ComponentProps<typeof Link> & { variant?: Variant; children: ReactNode }) {
  return (
    <Link className={buttonClass(variant, className)} {...props}>
      {children}
    </Link>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cn("size-4 animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
