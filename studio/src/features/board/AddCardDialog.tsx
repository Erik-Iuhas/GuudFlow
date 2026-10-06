import { useState, type FormEvent } from "react";
import { Button } from "../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
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
}

/** Add a todo card on behalf of a human (`POST /cards`). Size L is refused: split the work. */
export function AddCardDialog({ runId, roles, open, onOpenChange, onAdded }: Props) {
  const add = useAddCard(runId);
  const [owner, setOwner] = useState("");
  const [title, setTitle] = useState("");
  const [items, setItems] = useState<string[]>([""]);
  const [kind, setKind] = useState<string>("code");
  const [size, setSize] = useState<string>("S");
  const [error, setError] = useState<string | null>(null);

  function reset(): void {
    setOwner("");
    setTitle("");
    setItems([""]);
    setKind("code");
    setSize("S");
    setError(null);
    add.reset();
  }

  async function submit(e: FormEvent): Promise<void> {
    e.preventDefault();
    const ownerRole = owner || roles[0] || "";
    const acceptance = items.map((i) => i.trim()).filter(Boolean);
    if (!ownerRole) return setError("Pick an owner.");
    if (title.trim().length < MIN_TITLE) return setError(`The title needs at least ${MIN_TITLE} characters.`);
    if (acceptance.length === 0) return setError("Add at least one acceptance item.");
    setError(null);
    try {
      const card = await add.mutateAsync({ owner: ownerRole, title: title.trim(), acceptance, kind, size });
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
      <DialogContent aria-describedby="add-card-desc">
        <DialogHeader>
          <DialogTitle>Add card</DialogTitle>
          <DialogDescription id="add-card-desc">
            A card lands in To do and shows up in the owner's next brief.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <label className="grid gap-1 text-sm">
            Owner
            <select
              className={FIELD}
              value={owner || roles[0] || ""}
              onChange={(e) => setOwner(e.target.value)}
              aria-label="Owner"
            >
              {roles.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </label>
          <label className="grid gap-1 text-sm">
            Title
            <input
              className={FIELD}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              aria-label="Title"
              placeholder="What should the owner do?"
            />
          </label>
          <fieldset className="grid gap-1 border-0 p-0 text-sm">
            <legend className="mb-1 p-0">Acceptance items</legend>
            {items.map((item, i) => (
              <div key={i} className="flex gap-1">
                <input
                  className={FIELD}
                  value={item}
                  aria-label={`Acceptance item ${i + 1}`}
                  placeholder="A testable condition"
                  onChange={(e) => setItems(items.map((x, j) => (j === i ? e.target.value : x)))}
                />
                {items.length > 1 && (
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label={`Remove acceptance item ${i + 1}`}
                    onClick={() => setItems(items.filter((_, j) => j !== i))}
                  >
                    <span aria-hidden>×</span>
                  </Button>
                )}
              </div>
            ))}
            <div>
              <Button variant="ghost" size="sm" onClick={() => setItems([...items, ""])}>
                Add item
              </Button>
            </div>
          </fieldset>
          <div className="grid grid-cols-2 gap-3">
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
          <p className="m-0 text-xs text-muted-foreground">
            Size L is not allowed: split the work into S or M cards.
          </p>
          {error && (
            <p role="alert" className="m-0 text-sm text-destructive">
              {error}
            </p>
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
