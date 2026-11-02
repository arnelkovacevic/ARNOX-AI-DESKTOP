import { cn } from "@/lib/utils";
import type { JarvisState } from "@/hooks/use-jarvis-socket";

export function Reactor({ state }: { state: JarvisState }) {
  return (
    <div className={cn("reactor", `reactor-${state.toLowerCase()}`)} aria-label={`ARNOX state: ${state}`} role="img">
      <div className="reactor-orbit orbit-a" />
      <div className="reactor-orbit orbit-b" />
      <div className="reactor-spokes" />
      <div className="reactor-core"><span>J</span></div>
    </div>
  );
}
