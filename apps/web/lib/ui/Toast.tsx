"use client";

import { CircleAlert, CircleCheck, X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { cn } from "./cn";

type Toast = { id: number; message: string; tone: "ok" | "attention" | "error"; action?: { label: string; onClick: () => void } };

const Ctx = createContext<(t: Omit<Toast, "id">) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = (id: number) => setToasts((ts) => ts.filter((t) => t.id !== id));
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((ts) => [...ts.slice(-2), { ...t, id }]);
    setTimeout(() => dismiss(id), t.tone === "error" ? 9000 : 5000);
  }, []);

  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex flex-col items-center gap-2 p-4" aria-live="polite" role="status">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "pointer-events-auto flex w-full max-w-md animate-slide-up items-start gap-3 rounded-control px-4 py-3 text-[15px] shadow-pop",
              t.tone === "ok" && "bg-forest-900 text-white",
              t.tone === "attention" && "border border-amber-100 bg-amber-50 text-amber-700",
              t.tone === "error" && "border border-red-700/20 bg-red-50 text-red-700",
            )}
          >
            {t.tone === "ok" ? <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden="true" /> : <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
            <span className="flex-1">{t.message}</span>
            {t.action ? (
              <button type="button" className="font-semibold underline underline-offset-2" onClick={() => (t.action!.onClick(), dismiss(t.id))}>
                {t.action.label}
              </button>
            ) : null}
            <button type="button" onClick={() => dismiss(t.id)} aria-label="Dismiss" className="-m-1 p-1 opacity-70 hover:opacity-100">
              <X className="size-4" />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
