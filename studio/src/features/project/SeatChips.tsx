import { Badge } from "../../components/ui/badge";
import type { ProjectSeats } from "../../types";

/** The seats a person holds on this project: Owner always, Lead and Reviewer when on. */
export function SeatChips({ seats }: { seats?: ProjectSeats }) {
  const chips = ["Owner"];
  if (seats?.lead) chips.push("Lead");
  if (seats?.reviewer && seats.reviewer !== "off") chips.push("Reviewer");
  return (
    <ul className="m-0 flex list-none flex-wrap items-center gap-1 p-0" aria-label="Your seats" data-testid="seat-chips">
      {chips.map((c) => (
        <li key={c}>
          <Badge variant="outline">{c}</Badge>
        </li>
      ))}
    </ul>
  );
}
