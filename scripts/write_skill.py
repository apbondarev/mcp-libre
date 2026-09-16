#!/usr/bin/env python3
"""Write the skill that ships beside the server, from the server's own guide.

The MCP `instructions` and the skill say the same thing, so they are written
once — in plugin/pythonpath/mcp_guide.py — and this puts them where a Claude
Code skill lives. tests/test_guide.py fails if the file on disk has drifted
from what this would write.

    python3 scripts/write_skill.py            # write it
    python3 scripts/write_skill.py --check    # only say whether it is current
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugin", "pythonpath"))

SKILL_PATH = os.path.join(REPO, "skills", "libreoffice-mcp", "SKILL.md")

FRONT_MATTER = """\
---
name: libreoffice-mcp
description: Use when editing a document that is open in LibreOffice through the libreoffice MCP server - addressing text by paragraph, block, table cell, selection or anchor; keeping runs, links, comments and pictures through a rewrite; comments, tables, spelling and language; batching a plan into one undo step. Not for driving LibreOffice from the command line.
---

# Editing an open LibreOffice document through the MCP server

"""

TAIL = """\

## Checking your work

- `read_runs` the stretch you rewrote and confirm the styles, links and
  languages are the ones you meant to keep.
- `describe_table` with `runs: true` beside the table you copied the look
  from, when the work was a table.
- `render_page` on the page it landed on, to look at it as the reader will.
- Leave the document unsaved unless asked: that keeps Ctrl+Z available, and
  every call above is one undo step.

## Where the detail is

Every claim here was measured against a real LibreOffice and is recorded,
with the measurement, in the server repository's CLAUDE.md. When a tool
behaves unexpectedly, read the refusal it gives first: it names what it
would have destroyed and the route that keeps it.
"""


def skill_text():
    from mcp_guide import INSTRUCTIONS
    return FRONT_MATTER + INSTRUCTIONS + TAIL


def main():
    wanted = skill_text()
    checking = "--check" in sys.argv
    current = None
    if os.path.exists(SKILL_PATH):
        with open(SKILL_PATH, encoding="utf-8") as handle:
            current = handle.read()
    if current == wanted:
        print(f"{SKILL_PATH} is current")
        return 0
    if checking:
        print(f"{SKILL_PATH} is out of date — run scripts/write_skill.py")
        return 1
    os.makedirs(os.path.dirname(SKILL_PATH), exist_ok=True)
    with open(SKILL_PATH, "w", encoding="utf-8") as handle:
        handle.write(wanted)
    print(f"wrote {SKILL_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
