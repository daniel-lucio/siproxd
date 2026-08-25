#!/usr/bin/env python3
"""Apply stage1-sighup.patch despite its original bare @@ hunk headers.

The original Stage 1 patch contains valid diff bodies but malformed hunk
headers. This helper applies each hunk by exact context matching and refuses
to write any files if a hunk is ambiguous or no longer matches the source.

Hunks are applied bottom-to-top within each file. This is important because
some adjacent hunks in the original patch have overlapping context; applying
them top-to-bottom would make a later hunk fail against text changed by an
earlier hunk.

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


def hunk_old_new(lines):
    old = "".join(line[1:] for line in lines if line.startswith((" ", "-")))
    new = "".join(line[1:] for line in lines if line.startswith((" ", "+")))
    return old, new


def locate_hunk(source: str, lines, path: str, number: int):
    old, new = hunk_old_new(lines)

    if not old:
        die(f"{path}: hunk {number} has no context/deletion lines")

    positions = []
    start = 0
    while True:
        pos = source.find(old, start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + 1

    if not positions:
        # A previous run may already have applied this hunk. Treat that as
        # success only if the complete replacement text occurs exactly once.
        new_count = source.count(new)
        if new_count == 1:
            return None, old, new
        die(f"{path}: hunk {number} does not match the current source")

    if len(positions) > 1:
        die(f"{path}: hunk {number} is ambiguous ({len(positions)} exact matches)")

    return positions[0], old, new


def apply_file(source: str, hunks, path: str) -> str:
    # Locate all hunks against the pristine source first. This guarantees that
    # overlapping context in one hunk cannot invalidate another hunk merely
    # because of application order.
    located = []
    for number, lines in enumerate(hunks, 1):
        pos, old, new = locate_hunk(source, lines, path, number)
        if pos is not None:
            located.append((pos, number, old, new))

    # Apply from the bottom of the file upward so byte offsets above an edit
    # remain valid. Refuse true edit-range overlap; context overlap is fine.
    located.sort(key=lambda item: item[0], reverse=True)
    last_start = len(source) + 1
    for pos, number, old, new in located:
        edit_end = pos + len(old)
        if edit_end > last_start:
            # The malformed source patch can contain overlapping context, but
            # two actual replacement ranges should not overlap. If they do,
            # stop rather than guess.
            die(f"{path}: hunk {number} replacement overlaps another hunk")
        source = source[:pos] + new + source[edit_end:]
        last_start = pos

    return source


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
        pending[p] = apply_file(source, entry["hunks"], path)

    # Only write after every hunk in every file has been validated/applied in memory.
    for p, source in pending.items():
        p.write_text(source, encoding="utf-8")
        print(f"updated {p}")

    print("Stage 1 source changes applied successfully.")
    print("Review with: git diff -- src/siproxd.c src/readconf.c src/sock.c")


if __name__ == "__main__":
    main()
