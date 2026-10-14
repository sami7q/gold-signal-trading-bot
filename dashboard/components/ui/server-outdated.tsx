"use client";

// botup.md §V12: the dashboard (Vercel) can be newer than the bot on the server. A page whose API route is missing
// says so plainly -- instead of a bare "Not Found".

import { ServerCog } from "lucide-react";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";

export function ServerOutdated({ what }: { what: string }) {
  return (
    <Card>
      <EmptyState
        icon={ServerCog}
        title="البوت على السيرفر بإصدار أقدم من هذه اللوحة"
        description={`${what} موجودة في اللوحة لكن ليس بعد في البوت على السيرفر. على السيرفر نفّذ: loco_copy\\deploy_server.ps1 (botup.md §V12) — يسحب التحديث، يختبر، يعيد تشغيل البوت والواجهة، ويستورد نتائج البحث. بعدها حدّث الصفحة.`}
      />
    </Card>
  );
}
