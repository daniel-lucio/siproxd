#!/usr/bin/env python3
"""Apply stage1-sighup.patch despite its original bare @@ hunk headers.

The original Stage 1 patch contains valid diff bodies but malformed hunk
headers. This helper applies each hunk by exact context matching and refuses
to write any files if a hunk is ambiguous or no longer matches the source.

If a full hunk context no longer matches (for example because local whitespace
or an overlapping neighboring hunk differs), the helper falls back to the
smallest changed block and still requires that block to occur exactly once.

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


def minimal_change_old_new(lines):
    changed = [i for i, line in enumerate(lines) if line.startswith(("+", "-"))]
    if not changed:
        return "", ""
    first, last = changed[0], changed[-1]
    core = lines[first:last + 1]
    old = "".join(line[1:] for line in core if line.startswith((" ", "-")))
    new = "".join(line[1:] for line in core if line.startswith((" ", "+")))
    return old, new


def unique_position(source: str, needle: str):
    if not needle:
        return None, 0
    positions = []
    start = 0
    while True:
        pos = source.find(needle, start)
        if pos < 0:
            break
        positions.append(pos)
        start = pos + 1
    return (positions[0] if len(positions) == 1 else None), len(positions)


def locate_hunk(source: str, lines, path: str, number: int):
    old, new = hunk_old_new(lines)
    if not old:
        die(f"{path}: hunk {number} has no context/deletion lines")

    pos, count = unique_position(source, old)
    if count == 1:
        return pos, old, new
    if count > 1:
        die(f"{path}: hunk {number} is ambiguous ({count} exact matches)")

    # If already applied, accept it idempotently.
    _, new_count = unique_position(source, new)
    if new_count == 1:
        return None, old, new

    # Fallback: use only the actual changed block, dropping surrounding
    # context. This is safe only when the old changed block is unique.
    core_old, core_new = minimal_change_old_new(lines)
    if core_old:
        core_pos, core_count = unique_position(source, core_old)
        if core_count == 1:
            print(f"note: {path}: hunk {number} matched by unique changed block")
            return core_pos, core_old, core_new
        if core_count > 1:
            die(f"{path}: hunk {number} changed block is ambiguous ({core_count} matches)")

    die(f"{path}: hunk {number} does not match the current source")


def apply_file(source: str, hunks, path: str) -> str:
    # Locate all edits against the pristine source, then apply from bottom to
    # top so earlier byte offsets are not shifted by later edits.
    located = []
    for number, lines in enumerate(hunks, 1):
        pos, old, new = locate_hunk(source, lines, path, number)
        if pos is not None:
            located.append((pos, number, old, new))

    located.sort(key=lambda item: item[0], reverse=True)
    for pos, number, old, new in located:
        # Re-check at the recorded offset. If a lower edit touched this exact
        # range, fail instead of guessing.
        if source[pos:pos + len(old)] != old:
            die(f"{path}: hunk {number} overlaps another replacement")
        source = source[:pos] + new + source[pos + len(old):]

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

    # Only write after every hunk in every file has validated in memory.
    for p, source in pending.items():
        p.write_text(source, encoding="utf-8")
        print(f"updated {p}")

    print("Stage 1 source changes applied successfully.")
    print("Review with: git diff -- src/siproxd.c src/readconf.c src/sock.c")


if __name__ == "__main__":
    main()
