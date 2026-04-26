import { useRef, useState } from "react";

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
      className={`dropzone ${dragOver ? "dropzone-active" : ""} ${
        disabled || busy ? "dropzone-disabled" : ""
      }`}
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
        style={{ display: "none" }}
        onChange={(e) => void handleFiles(e.target.files)}
        disabled={disabled || busy}
      />
      <div className="dropzone-body">
        <p className="muted">
          {busy
            ? "Uploading…"
            : dragOver
              ? "Release to upload"
              : "Drag files here, or"}
        </p>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={disabled || busy}
        >
          Browse files
        </button>
      </div>
    </div>
  );
}
