import { cn } from "@/lib/format";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded border border-border bg-ink px-2.5 py-1.5 text-sm text-bone placeholder:text-muted-dim",
        "focus:border-gold-dim focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "w-full rounded border border-border bg-ink px-2.5 py-1.5 text-sm text-bone placeholder:text-muted-dim",
        "focus:border-gold-dim focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "rounded border border-border bg-ink px-2.5 py-1.5 text-sm text-bone",
        "focus:border-gold-dim focus:outline-none",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn("mb-1 block text-xs font-medium uppercase tracking-wide text-muted", className)}
      {...props}
    />
  );
}
