# prebuilt/

Drop a prebuilt Studio here when this machine cannot install Studio's npm packages:

- `studio-dist.tar.gz` (from the **Studio bundle** GitHub Action's `studio-latest` release, or made
  with `bash scripts/run.sh studio-pack` on a machine with npm), or
- the GitHub Actions artifact zip as downloaded (`studio-dist-tar-gz.zip`).

Every script (Prepare, Setup, Run dev stack) checks this folder first and skips npm when it finds
one. Files here are git-ignored.

Python packages work the same way when the proxy blocks PyPI: put the **Python wheels** GitHub
Action's artifact zip for this machine here (`python-wheels-windows-py311.zip`,
`python-wheels-linux-py311.zip` for Linux and WSL, or the `-py312` ones if that is your Python).
The **Python environment** task (also run by Prepare, Setup and Run dev stack) unpacks it to
`prebuilt/wheels/` and installs with no network. A folder of `.whl` files in `prebuilt/wheels/`
works too.
