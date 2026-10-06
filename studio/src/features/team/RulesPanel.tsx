import { useState } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Alert, Input, Select } from "../../components/ui/form-controls";
import { useSpec, type RuleWire } from "../plan/api";
import { adherenceText, useAdherence, useRuleEdit } from "../spec/api";

/** Where a rule came from, in words: "from D2", "you" or "project default". */
export function ruleSource(source: string): string {
  if (/^[DR]\d+/.test(source)) return `from ${source}`;
  if (["human", "you", "user", "owner"].includes(source.toLowerCase())) return "you";
  return "project default";
}

const isDecisionOwned = (r: RuleWire) => /^D\d+/.test(r.source);

/** "Rules the team follows" plus how well they were followed lately (project flow plan §6, §7). */
export function RulesPanel({ projectId }: { projectId: string }) {
  const spec = useSpec(projectId);
  const adherence = useAdherence(projectId);
  const edit = useRuleEdit(projectId);
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState("");
  const [applies, setApplies] = useState("*");
  const [strength, setStrength] = useState<"binding" | "preference">("binding");
  const rules = (spec.data?.rules ?? []).filter((r) => r.status === "active");

  function add() {
    if (!text.trim()) return;
    const applies_to = applies.split(",").map((a) => a.trim()).filter(Boolean);
    edit.mutate(
      { op: "add_rule", text: text.trim(), applies_to: applies_to.length ? applies_to : ["*"], strength },
      { onSuccess: () => { setText(""); setApplies("*"); setAdding(false); } },
    );
  }

  return (
    <Card className="mb-3 p-4" data-testid="rules-panel">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="m-0 text-base font-semibold">Rules the team follows</h2>
        {!adding && <Button size="sm" variant="outline" onClick={() => setAdding(true)}>+ Rule</Button>}
      </div>
      {edit.error && <Alert variant="error" className="mb-2">{edit.error.message}</Alert>}
      {adding && (
        <div className="mb-3 grid gap-2 rounded-md border border-border p-2">
          <Input aria-label="Rule text" placeholder="e.g. Every endpoint has a test" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Input aria-label="Applies to" className="w-48" placeholder="applies to (* = everyone)" value={applies} onChange={(e) => setApplies(e.target.value)} />
            <Select aria-label="Strength" className="w-40" value={strength} onChange={(e) => setStrength(e.target.value as "binding" | "preference")}>
              <option value="binding">Binding</option>
              <option value="preference">Preference</option>
            </Select>
            <Button size="sm" onClick={add} disabled={edit.isPending || !text.trim()}>Add rule</Button>
            <Button size="sm" variant="outline" onClick={() => setAdding(false)}>Cancel</Button>
          </div>
        </div>
      )}
      {rules.length === 0 ? (
        <p className="m-0 text-sm text-muted-foreground">No rules yet.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {rules.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2 text-sm" data-testid={`rule-${r.id}`}>
              <span className="min-w-0 flex-1">{r.text}</span>
              <span className="text-xs text-muted-foreground">{ruleSource(r.source)}</span>
              <span className="text-xs text-muted-foreground">applies to {r.applies_to.join(", ")}</span>
              <Badge variant={r.strength === "binding" ? "blocked" : "outline"}>{r.strength}</Badge>
              {isDecisionOwned(r) ? (
                <span className="text-xs text-muted-foreground">change {r.source} in the plan</span>
              ) : (
                <Button size="sm" variant="outline" disabled={edit.isPending} aria-label={`Drop rule: ${r.text}`}
                  onClick={() => edit.mutate({ op: "drop_rule", id: r.id, reason: "Dropped in the Team tab" })}>
                  Drop
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {adherence.data && (
        <section className="mt-3 border-t border-border pt-2" aria-label="Adherence">
          <h3 className="m-0 mb-0.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Adherence</h3>
          <p className="m-0 text-sm" data-testid="adherence">{adherenceText(adherence.data)}</p>
        </section>
      )}
    </Card>
  );
}
