import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "./cn";

type Variant = "primary" | "secondary" | "ghost" | "dark" | "danger";

// Sharp, and alive under the pointer: every button eases its colour, lifts a pixel on hover and
// settles on press, so it reads as something that will act. Motion is dropped for people who ask
// for less (`prefers-reduced-motion` in globals.css).
const base =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-control px-5 text-[15px] font-semibold whitespace-nowrap " +
  "transition-[background-color,border-color,color,box-shadow,transform] duration-150 ease-out " +
  "hover:-translate-y-px active:translate-y-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-forest-700 " +
  "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0 disabled:hover:shadow-none";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover hover:shadow-lift active:bg-accent-strong",
  secondary: "border border-line bg-surface text-forest-900 hover:border-forest-700 hover:text-forest-700",
  ghost: "text-ink-2 hover:bg-mint-100 hover:text-ink",
  dark: "bg-accent-strong text-white hover:bg-accent-hover hover:shadow-lift",
  danger: "border border-line bg-surface text-red-700 hover:border-red-700 hover:bg-red-50",
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
