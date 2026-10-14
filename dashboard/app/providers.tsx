"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { livePoll } from "@/lib/live";

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            // 5 s polling until the live stream is up (botup.md §V11), then a 60 s safety net: the stream
            // refetches what changed the moment it changes
            refetchInterval: livePoll(5_000),
            retry: 1,
          },
        },
      }),
  );

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
