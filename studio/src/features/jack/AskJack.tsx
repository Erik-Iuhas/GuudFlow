import { Button } from "../../components/ui/button";
import { usePmName } from "../pm/api";
import { useJack, type JackPlace } from "./context";

/** "Ask Jack" where something is stuck (docs/JACK_UNSTUCK_PLAN.md §6): opens the dock with the
 * place set and a starter question to send or edit. */
export function AskJack({ question, place, label }: { question: string; place?: JackPlace; label?: string }) {
  const { ask } = useJack();
  const pmName = usePmName();
  return (
    <Button type="button" size="sm" variant="outline" className="h-7 px-2 text-xs" data-testid="ask-jack"
      onClick={() => ask(question, place)}>
      {label ?? `Ask ${pmName}`}
    </Button>
  );
}
