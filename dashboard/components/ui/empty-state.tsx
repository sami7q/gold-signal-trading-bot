import type { LucideIcon } from "lucide-react";

export function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
      {Icon ? <Icon className="h-6 w-6 text-muted-dim" strokeWidth={1.5} /> : null}
      <p className="text-sm font-medium text-muted">{title}</p>
      {description ? <p className="max-w-xs text-xs text-muted-dim">{description}</p> : null}
    </div>
  );
}

export function LoadingState() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-gold" />
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-12 text-center">
      <p className="text-sm font-medium text-loss-bright">تعذّر تحميل هذه البيانات</p>
      <p className="max-w-sm text-xs text-muted-dim">{message}</p>
    </div>
  );
}
