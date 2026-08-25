#!/usr/bin/env python3
"""Apply stage1-sighup.patch despite its original bare @@ hunk headers.

The original Stage 1 patch contains valid diff bodies but malformed hunk
headers.  This helper applies each hunk by exact context matching and refuses
to continue if a hunk is ambiguous or no longer matches the source tree.

Run from the repository root on branch feature/sighup-reload:
    python3 apply-stage1.py
"""

from pathlib import Path
import sys

PATCH = Path("stage1-sighup.patch")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def parse_patch(text: str):
    files = []
    current = None
    hunk = None

    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current is not None:
                files.append(current)
            current = {"path": None, "hunks": []}
            hunk = None
            continue
        if current is None:
            continue
        if line.startswith("+++ b/"):
            current["path"] = line[len("+++ b/"):].rstrip("\r\n")
            continue
        if line.startswith("@@"):
            hunk = []
            current["hunks"].append(hunk)
            continue
        if hunk is not None and line[:1] in (" ", "+", "-"):
            hunk.append(line)

    if current is not None:
        files.append(current)
    return files


def apply_hunk(source: str, lines, path: str, number: int) -> str:
    old = "".join(line[1:] for line in lines if line.startswith((" ", "-")))
    new = "".join(line[1:] for line in lines if line.startswith((" ", "+")))

    if not old:
        die(f"{path}: hunk {number} has no context/deletion lines")

    count = source.count(old)
    if count == 0:
        die(f"{path}: hunk {number} does not match the current source")
    if count > 1:
        die(f"{path}: hunk {number} is ambiguous ({count} exact matches)")

    return source.replace(old, new, 1)


def main() -> None:
    if not PATCH.exists():
        die(f"{PATCH} not found; run this from the repository root")

    patch_text = PATCH.read_text(encoding="utf-8")
    entries = parse_patch(patch_text)
    if not entries:
        die("no file diffs found in patch")

    pending = {}
    for entry in entries:
        path = entry["path"]
        if not path:
            die("patch contains a file diff without a +++ b/<path> line")
        p = Path(path)
        if not p.exists():
            die(f"target file {path} does not exist")

        source = p.read_text(encoding="utf-8")
        for n, hunk in enumerate(entry["hunks"], 1):
            source = apply_hunk(source, hunk, path, n)
        pending[p] = source

    # Only write after every hunk in every file has been validated/applied in memory.
    for p, source in pending.items():
        p.write_text(source, encoding="utf-8")
        print(f"updated {p}")

    print("Stage 1 source changes applied successfully.")
    print("Review with: git diff -- src/siproxd.c src/readconf.c src/sock.c")


if __name__ == "__main__":
    main()
