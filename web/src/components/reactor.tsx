import type { JarvisState } from "@/hooks/use-jarvis-socket";
import { ArcReactor } from "@/components/arc-reactor";

export function Reactor({ state }: { state: JarvisState }) {
  return <ArcReactor state={state} />;
}
