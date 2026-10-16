import { useEffect, useState, type FormEvent } from "react";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import type { CardView } from "../../stores/runEvents";
import type { SpecWire } from "../plan/api";
import { useAddCard } from "./useCardMutations";

export const CARD_KINDS = ["code", "test", "docs", "research", "visual"] as const;
const MIN_TITLE = 5;

const FIELD =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

interface Props {
  runId: string;
  roles: readonly string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdded?: (cardId: string) => void;
  /** The project's spec, to pick the checks the card closes (worker context plan F2). */
  spec?: SpecWire;
  /** The board's cards, to pick what the new card waits on. */
  cards?: readonly CardView[];
  /** Prefilled from a lane's quick-add row. */
  initialOwner?: string;
  initialTitle?: string;
}

/** The open checks of the spec, as `R1.1: text` lines. */
export function checkLines(spec: SpecWire | undefined): { id: string; line: string }[] {
  return (spec?.requirements ?? [])
    .filter((r) => (r as { status?: string }).status !== "dropped")
    .flatMap((r) => r.checks.map((c) => ({ id: c.id, line: `${c.id}: ${c.text}` })));
}

/** Add a todo card on behalf of a human (`POST /cards`). Size L is refused: split the work. */
export function AddCardDialog({ runId, roles, open, onOpenChange, onAdded, spec, cards = [],
  initialOwner, initialTitle }: Props) {
  const add = useAddCard(runId);
  const [owner, setOwner] = useState("");
  const [title, setTitle] = useState("");
  const [acceptance, setAcceptance] = useState("");
  const [serves, setServes] = useState<string[]>([]);
  const [dependsOn, setDependsOn] = useState<string[]>([]);
  const [kind, setKind] = useState<string>("code");
  const [size, setSize] = useState<string>("S");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      if (initialOwner) setOwner(initialOwner);
      if (initialTitle) setTitle(initialTitle);
    }
  }, [open, initialOwner, initialTitle]);

  function reset(): void {
    setOwner("");
    setTitle("");
    setAcceptance("");
    setServes([]);
    setDependsOn([]);
    setKind("code");
    setSize("S");
    setError(null);
    add.reset();
  }

  const checks = checkLines(spec);
  const openCards = cards.filter((c) => c.column !== "done");
  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  async function submit(e: FormEvent): Promise<void> {
    e.preventDefault();
    const ownerRole = owner || roles[0] || "";
    const typed = acceptance.split("\n").map((i) => i.trim()).filter(Boolean);
    const checkItems = checks.filter((c) => serves.includes(c.id)).map((c) => c.line)
      .filter((line) => !typed.includes(line));
    const items = [...typed, ...checkItems];
    if (!ownerRole) return setError("Pick an owner.");
    if (title.trim().length < MIN_TITLE) return setError(`The title needs at least ${MIN_TITLE} characters.`);
    if (items.length === 0) return setError("Add at least one acceptance item or pick a check.");
    setError(null);
    try {
      const card = await add.mutateAsync({
        owner: ownerRole, title: title.trim(), acceptance: items, kind, size,
        ...(serves.length ? { serves } : {}), ...(dependsOn.length ? { depends_on: dependsOn } : {}),
      });
      onAdded?.(card.id);
      reset();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the card.");
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent aria-describedby="add-card-desc" className="max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Add card</DialogTitle>
          <DialogDescription id="add-card-desc">
            A card lands in To do and shows up in the owner's next brief.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1 text-sm">
              Owner
              <select className={FIELD} value={owner || roles[0] || ""} onChange={(e) => setOwner(e.target.value)} aria-label="Owner">
                {roles.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="grid gap-1 text-sm">
                Kind
                <select className={FIELD} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
                  {CARD_KINDS.map((k) => (
                    <option key={k} value={k}>{k}</option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-sm">
                Size
                <select className={FIELD} value={size} onChange={(e) => setSize(e.target.value)} aria-label="Size">
                  <option value="S">S</option>
                  <option value="M">M</option>
                  <option value="L" disabled>L (too big)</option>
                </select>
              </label>
            </div>
          </div>
          <label className="grid gap-1 text-sm">
            Title
            <input className={FIELD} value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title"
                   placeholder="What should the owner do?" />
          </label>
          <label className="grid gap-1 text-sm">
            Acceptance (one item per line)
            <textarea className={`${FIELD} min-h-[5rem]`} value={acceptance} aria-label="Acceptance"
                      placeholder={"A testable condition\nAnother one"}
                      onChange={(e) => setAcceptance(e.target.value)} />
          </label>
          {checks.length > 0 && (
            <fieldset className="grid max-h-40 gap-1 overflow-y-auto rounded-md border border-border p-2 text-sm">
              <legend className="px-1 text-xs text-muted-foreground">Checks this card closes (their lines are added)</legend>
              {checks.map((c) => (
                <label key={c.id} className="flex items-start gap-2 text-xs">
                  <input type="checkbox" checked={serves.includes(c.id)} onChange={() => setServes(toggle(serves, c.id))}
                         aria-label={`Closes ${c.id}`} />
                  <span><span className="font-mono">{c.id}</span> {c.line.slice(c.id.length + 2)}</span>
                </label>
              ))}
            </fieldset>
          )}
          {openCards.length > 0 && (
            <fieldset className="grid max-h-32 gap-1 overflow-y-auto rounded-md border border-border p-2 text-sm">
              <legend className="px-1 text-xs text-muted-foreground">Waits on</legend>
              {openCards.map((c) => (
                <label key={c.id} className="flex items-center gap-2 text-xs">
                  <input type="checkbox" checked={dependsOn.includes(c.id)} onChange={() => setDependsOn(toggle(dependsOn, c.id))}
                         aria-label={`Waits on ${c.id}`} />
                  <span><span className="font-mono">{c.id}</span> {c.title}</span>
                </label>
              ))}
            </fieldset>
          )}
          <p className="m-0 text-xs text-muted-foreground">Size L is not allowed: split the work into S or M cards.</p>
          {error && (
            <p role="alert" className="m-0 text-sm text-destructive">{error}</p>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={add.isPending}>
              {add.isPending ? "Creating..." : "Create card"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
