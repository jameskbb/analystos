import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Spinner({ className, label = "Loading" }: { className?: string; label?: string }) {
  return <Loader2 aria-label={label} role="status" className={cn("size-3.5 animate-spin text-fg-subtle", className)} />;
}
