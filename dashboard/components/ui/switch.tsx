"use client";

import { cn } from "@/lib/format";

export function Switch({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border px-0.5 transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        checked ? "border-gold-dim bg-gold/30" : "border-border bg-surface-raised",
      )}
    >
      <span
        className={cn(
          "inline-block h-3.5 w-3.5 transform rounded-full bg-bone transition-transform",
          checked ? "translate-x-4 bg-gold-bright" : "translate-x-0",
        )}
      />
    </button>
  );
}
