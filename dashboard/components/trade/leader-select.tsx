"use client";

import { Label, Select } from "@/components/ui/input";
import { useLeaders } from "@/hooks/use-loco";
import { leaderName } from "@/lib/format";

/** "كل المتداولين / Loco / 3ea" -- filters a page through the API's `leader=` parameter ("" = all). */
export function LeaderSelect({ value, onChange }: { value: string; onChange: (leader: string) => void }) {
  const leaders = useLeaders();
  if ((leaders.data?.length ?? 0) < 2) return null;
  return (
    <div>
      <Label>المتداول</Label>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">كل المتداولين</option>
        {leaders.data?.map((l) => (
          <option key={l.label} value={l.label}>{leaderName(l.label)}</option>
        ))}
      </Select>
    </div>
  );
}
