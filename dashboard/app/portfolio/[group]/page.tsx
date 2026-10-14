"use client";

import { useParams } from "next/navigation";
import { PortfolioView } from "@/components/portfolio/portfolio-view";

/** botup.md §V10: the other simulator sections (/portfolio/mix2ac4, /portfolio/mix2396). */
export default function PortfolioGroupPage() {
  const params = useParams<{ group: string }>();
  const group = decodeURIComponent(params.group ?? "trio").toLowerCase();
  return <PortfolioView key={group} group={group} />;
}
