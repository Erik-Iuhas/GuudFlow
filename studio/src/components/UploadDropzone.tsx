import { useRef, useState } from "react";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";

export interface UploadDropzoneProps {
  onUpload: (files: File[]) => Promise<void>;
  disabled?: boolean;
}

export function UploadDropzone({ onUpload, disabled }: UploadDropzoneProps) {
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  async function handleFiles(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    const files = Array.from(fileList);
    setBusy(true);
    try {
      await onUpload(files);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div
      className={cn(
        "rounded-lg border-2 border-dashed border-input bg-muted p-6 text-center transition-colors",
        dragOver && "border-primary bg-accent",
        (disabled || busy) && "pointer-events-none opacity-60",
      )}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled && !busy) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (disabled || busy) return;
        void handleFiles(e.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => void handleFiles(e.target.files)}
        disabled={disabled || busy}
      />
      <div>
        <p className="m-0 mb-2 text-muted-foreground">
          {busy
            ? "Uploading…"
            : dragOver
              ? "Release to upload"
              : "Drag files here, or"}
        </p>
        <Button
          type="button"
          variant="outline"
          onClick={() => inputRef.current?.click()}
          disabled={disabled || busy}
        >
          Browse files
        </Button>
      </div>
    </div>
  );
}
