import type { ReactNode } from "react";

export function Card({ title, action, children, className = "" }: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-stone-200 bg-white p-4 shadow-sm ${className}`}>
      {(title || action) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

const MODE_STYLES: Record<string, string> = {
  cash: "bg-emerald-100 text-emerald-800",
  upi: "bg-indigo-100 text-indigo-800",
  udhaar: "bg-amber-100 text-amber-900",
  open: "bg-sky-100 text-sky-800",
  awaiting_payment: "bg-orange-100 text-orange-800",
  cancelled: "bg-stone-200 text-stone-600",
};

export function Badge({ kind, children }: { kind: string; children?: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
        MODE_STYLES[kind] ?? "bg-stone-100 text-stone-700"
      }`}
    >
      {children ?? kind.replace("_", " ")}
    </span>
  );
}

export function Button({ children, onClick, variant = "primary", disabled, type = "button",
  className = "" }: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const styles = {
    primary: "bg-orange-600 text-white hover:bg-orange-700",
    secondary: "border border-stone-300 bg-white text-stone-800 hover:bg-stone-50",
    danger: "bg-rose-600 text-white hover:bg-rose-700",
    ghost: "text-stone-600 hover:bg-stone-100",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-stone-400">{children}</p>;
}
