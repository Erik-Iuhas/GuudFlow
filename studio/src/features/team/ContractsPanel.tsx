import { Alert, SectionTitle } from "../../components/ui/form-controls";
import { Badge } from "../../components/ui/badge";
import { useProjectContracts, type ContractWire } from "../design/api";

/**
 * The contract each role works under (role contracts plan §2): what it owns, when its work is
 * done, who checks it and its rules. Project overrides are marked; rules a person ticked from
 * the contract measures land there.
 */
export function ContractsPanel({ projectId }: { projectId: string }) {
  const data = useProjectContracts(projectId);
  if (data.error) return <Alert variant="error">{data.error.message}</Alert>;
  if (!data.data) return null;
  const byId = Object.fromEntries(data.data.contracts.map((c) => [c.id, c]));
  return (
    <div data-testid="contracts-panel">
      <SectionTitle>Role contracts</SectionTitle>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {data.data.team.map((t) => {
          const c = t.contract ? byId[t.contract] : undefined;
          return (
            <li key={t.role} className="rounded-md border border-border p-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-semibold">{t.name ? `${t.name} (${t.role})` : t.role}</span>
                {c ? <Badge variant="outline">{c.title}</Badge> : <Badge variant="secondary">no contract</Badge>}
                {c?.source === "project" && <Badge variant="waiting">project rules</Badge>}
              </div>
              {c && <ContractBody contract={c} titles={byId} />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ContractBody({ contract, titles }: { contract: ContractWire; titles: Record<string, ContractWire> }) {
  const title = (id: string) => titles[id]?.title ?? id.replace(/_/g, " ");
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-xs text-muted-foreground">{contract.purpose}</summary>
      <dl className="m-0 mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {contract.owns.length > 0 && <><dt className="font-semibold">Owns</dt><dd className="m-0">{contract.owns.join(", ").replace(/_/g, " ")}</dd></>}
        {contract.done_when.length > 0 && <><dt className="font-semibold">Done when</dt><dd className="m-0">{contract.done_when.join("; ")}</dd></>}
        {contract.hands_off.length > 0 && (
          <><dt className="font-semibold">Hands off</dt>
            <dd className="m-0">{contract.hands_off.map((h) => `${h.what} → ${title(h.to)}`).join("; ")}</dd></>
        )}
        {contract.checked_by && <><dt className="font-semibold">Checked by</dt><dd className="m-0">{title(contract.checked_by)}</dd></>}
        {contract.may_not.length > 0 && <><dt className="font-semibold">May not</dt><dd className="m-0">{contract.may_not.join("; ")}</dd></>}
        {contract.rules.length > 0 && (
          <><dt className="font-semibold">Rules</dt>
            <dd className="m-0"><ul className="m-0 pl-4">{contract.rules.map((r) => <li key={r}>{r}</li>)}</ul></dd></>
        )}
        {contract.measures.length > 0 && (
          <><dt className="font-semibold">Measured by</dt>
            <dd className="m-0">{contract.measures.map((m) => m.description).join("; ")}</dd></>
        )}
      </dl>
    </details>
  );
}
