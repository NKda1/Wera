import type { ComponentProps, ReactNode } from "react";
import { cn } from "./cn";

const control =
  "w-full rounded-control border border-line bg-white px-3.5 text-[15px] text-ink placeholder:text-ink-2/70 hover:border-mint-200 focus-visible:border-forest-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-forest-700/20 disabled:bg-mint-50 disabled:text-ink-2";

export function Label({ htmlFor, children, className }: { htmlFor?: string; children: ReactNode; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={cn("mb-1.5 block text-[14px] font-semibold", className)}>
      {children}
    </label>
  );
}

export function Help({ children, id, className }: { children: ReactNode; id?: string; className?: string }) {
  return (
    <p id={id} className={cn("mt-1.5 text-small text-ink-2", className)}>
      {children}
    </p>
  );
}

export function FieldError({ children, id }: { children?: ReactNode; id?: string }) {
  if (!children) return null;
  return (
    <p id={id} className="mt-1.5 text-small font-medium text-red-700" role="alert">
      {children}
    </p>
  );
}

export function Input({ className, invalid, ...props }: ComponentProps<"input"> & { invalid?: boolean }) {
  return <input className={cn(control, "min-h-11", invalid && "border-red-700", className)} aria-invalid={invalid || undefined} {...props} />;
}

export function Textarea({ className, invalid, ...props }: ComponentProps<"textarea"> & { invalid?: boolean }) {
  return <textarea className={cn(control, "min-h-24 py-2.5 leading-relaxed", invalid && "border-red-700", className)} aria-invalid={invalid || undefined} {...props} />;
}

export function Select({ className, children, ...props }: ComponentProps<"select">) {
  return (
    <div className="relative min-w-0 flex-1">
      <select className={cn(control, "min-h-11 appearance-none pr-9", className)} {...props}>
        {children}
      </select>
      <svg className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-ink-2" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

export function Checkbox({
  label,
  description,
  className,
  ...props
}: Omit<ComponentProps<"input">, "type"> & { label: ReactNode; description?: ReactNode }) {
  return (
    <label className={cn("flex cursor-pointer items-start gap-3 py-1", props.disabled && "cursor-not-allowed", className)}>
      <span className="relative mt-0.5 inline-flex size-5 shrink-0">
        <input type="checkbox" className="peer size-5 cursor-pointer appearance-none rounded-[5px] border border-line bg-white checked:border-forest-700 checked:bg-forest-700 disabled:cursor-not-allowed disabled:opacity-60" {...props} />
        <svg className="pointer-events-none absolute inset-0 m-auto size-3.5 text-white opacity-0 peer-checked:opacity-100" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="m3.5 8.5 3 3 6-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <span className="min-w-0">
        <span className="block text-[15px] font-semibold">{label}</span>
        {description ? <span className="block text-small text-ink-2">{description}</span> : null}
      </span>
    </label>
  );
}
