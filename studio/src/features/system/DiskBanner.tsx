import { useEffect, useState } from "react";
import { systemApi, type DiskStatus } from "../../api";
import { Alert } from "../../components/ui/form-controls";
import { Button } from "../../components/ui/button";

const gb = (b: number) => `${(b / 1024 ** 3).toFixed(1)} GB`;

/** Warns when the disk GuudFlow writes to is low, with a one-click prune (FIELD_NOTES F2). */
export function DiskBanner({ load = systemApi.disk, prune = systemApi.prune }: {
  load?: () => Promise<DiskStatus>;
  prune?: typeof systemApi.prune;
}) {
  const [disk, setDisk] = useState<DiskStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    load().then((d) => alive && setDisk(d)).catch(() => {});
    return () => {
      alive = false;
    };
  }, [load]);

  if (!disk?.low && !result) return null;
  return (
    <Alert variant={disk?.low ? "error" : "success"} className="mb-4" data-testid="disk-banner">
      {disk?.low ? (
        <>
          Disk space is low: {gb(disk.free_bytes)} free of {gb(disk.total_bytes)} where GuudFlow keeps its data. Sessions
          will not start below 1 GB.{" "}
          <Button size="sm" variant="outline" className="ml-2 h-7" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const r = await prune();
                setDisk(r.disk);
                setResult(`Freed ${gb(r.freed_bytes)}. ${r.steps.join("; ")}`);
              } catch (e) {
                setResult(String(e));
              } finally {
                setBusy(false);
              }
            }}>
            Prune Docker build cache
          </Button>
        </>
      ) : null}
      {result && <p className="m-0 mt-1 text-xs">{result}</p>}
    </Alert>
  );
}
