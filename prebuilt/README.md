# prebuilt/

Drop a prebuilt Studio here when this machine cannot install Studio's npm packages:

- `studio-dist.tar.gz` (from the **Studio bundle** GitHub Action's `studio-latest` release, or made
  with `bash scripts/run.sh studio-pack` on a machine with npm), or
- the GitHub Actions artifact zip as downloaded (`studio-dist-tar-gz.zip`).

Every script (Prepare, Setup, Run dev stack) checks this folder first and skips npm when it finds
one. Files here are git-ignored.
