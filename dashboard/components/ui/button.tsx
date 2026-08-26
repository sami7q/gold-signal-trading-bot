import { cn } from "@/lib/format";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANT_CLASSES: Record<Variant, string> = {
  primary: "bg-gold text-ink hover:bg-gold-bright disabled:hover:bg-gold",
  secondary:
    "bg-surface-raised text-bone border border-border hover:border-gold-dim hover:text-gold-bright",
  ghost: "text-muted hover:text-bone hover:bg-white/5",
  danger: "bg-loss/15 text-loss-bright border border-loss/40 hover:bg-loss/25",
};

const SIZE_CLASSES: Record<Size, string> = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-3.5 py-2 text-sm",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        VARIANT_CLASSES[variant],
        SIZE_CLASSES[size],
        className,
      )}
      {...props}
    />
  );
}
