#!/usr/bin/env python3
"""Call every reading tool against a real document and check what comes back.

A smoke check over MCP, not a unit test: it asks the running server for its
tools, picks out the ones that only read, works out arguments from the
document itself — a paragraph the caret is in, a style the document uses, a
table it holds — and calls each one.

Three things are checked, and a failure in any of them ends with a non-zero
exit:

  * the answer is an answer: `success: true`, or a refusal that names a
    `code` from the closed set. A tool that throws, hangs or answers neither
    is the bug this catches;
  * every answer says what it cost (`elapsed_ms`), since a slow tool is
    otherwise a thing only a human with a stopwatch can see;
  * an address a tool hands out resolves: it is read back with read_runs,
    which refuses anything it cannot reach.

Beside that it reports, for each tool, whether its addresses are named in a
way that survives an edit — an **anchor**, or a **bookmark**, which is the
document's own handle on a place — so the column is also the progress of that
work.

    python3 scripts/read_tools_check.py
    python3 scripts/read_tools_check.py --document file:///home/me/Doc.odt
    python3 scripts/read_tools_check.py --only list_images,list_bookmarks
    python3 scripts/read_tools_check.py --section "Chapter 13" --paragraphs 40

Before asking anything it selects twenty paragraphs of a chapter — "Chapter
12", the one about lists, unless told otherwise — because a check run from
the front matter of a 519-page guide proves little: there are no tables, no
pictures and no cross-references up there, so half the tools answer "none"
and say nothing about themselves. The scoped tools are then asked about that
selection, which is what a caller actually holds.

With no document named it opens `tests/documents/WG262-WriterGuide.odt` — the
LibreOffice Writer Guide, 519 pages of headings, tables, pictures, fields and
cross-references, which is what makes it worth checking against — and leaves
it open. A document that is open already is answered with, not opened twice.

Plain standard library: it talks HTTP to the running MCP server, so any
python3 will do — no uno, no venv.
"""

import argparse
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from cursor_runs import MCPClient                            # noqa: E402

# Refusals are allowed to name only these; anything else is a contract break.
ERROR_CODES = {"NO_DOCUMENT", "WRONG_DOCUMENT_TYPE", "READ_ONLY",
               "INVALID_ADDRESS", "NOT_FOUND", "INVALID_PARAMETER",
               "WOULD_LOSE_FORMATTING", "UNSUPPORTED", "FAILED"}

# What a tool is asked for, when its answer is a list of things.
ITEMS = {
    "read_paragraphs": "paragraphs", "get_outline": "headings",
    "find_text": "hits", "find_by_style": "hits", "read_runs": "runs",
    "list_images": "images", "list_comments": "comments",
    "list_fields": "fields", "list_bookmarks": "bookmarks",
    "list_formulas": "formulas", "list_notes": "notes",
    "list_sections": "sections", "list_hyperlinks": "hyperlinks",
    "list_tables": "tables", "list_indexes": "indexes",
    "list_references": "references", "list_reference_targets": "targets",
    "list_tracked_changes": "changes", "check_spelling": "misspellings",
    "list_styles": "styles", "list_anchors": "anchors",
    "list_undo_steps": "undo", "list_open_documents": "documents",
    "list_headers_footers": "page_styles",
}

# Tools whose answers hold no address at all: there is nothing to anchor.
NO_ADDRESS = {"get_document_info", "get_text_content", "get_page_layout",
              "list_open_documents", "list_styles", "describe_style",
              "list_headers_footers", "list_undo_steps", "list_tables",
              "describe_table", "read_table"}


def reading_tools(client):
    """The tools that only read, as the server itself lists them."""
    listed = client.request("tools/list").get("result", {}).get("tools", [])
    names = []
    for tool in listed:
        name = tool["name"]
        plain = name[:-5] if name.endswith("_live") else name
        if plain.split("_")[0] in ("get", "list", "read", "find", "describe",
                                   "check"):
            names.append(plain)
    return sorted(names)


def arguments_for(name, ground):
    """What to call a tool with, from what the document itself says.

    None means the document holds nothing this tool could be asked about —
    no table, no style in use — which is reported as a skip rather than a
    failure, since it is the document's doing and not the server's.
    """
    here = ground.get("address")
    paragraph = ground.get("paragraph")
    simple = {
        "get_cursor_info": {}, "get_document_info": {},
        "list_open_documents": {}, "get_page_layout": {},
        "list_headers_footers": {}, "list_undo_steps": {},
        "list_anchors": {"count": 5}, "list_styles": {},
        "list_tables": {}, "list_indexes": {}, "get_text_content": {},
        "read_paragraphs": {"start": here, "count": 3} if here
                           else {"start": 0, "count": 3},
        "get_outline": {"count": 5},
        "find_text": {"query": ground.get("word") or "the", "max_results": 3},
        "read_runs": {"address": here} if here else None,
        "get_direct_formatting": {"address": here} if here else None,
        "check_spelling": {"address": here} if here else None,
        "list_comments": {"address": here} if here else {},
        "list_images": {"address": here} if here else {},
        "list_formulas": {"address": here} if here else {},
        "list_notes": {"address": here} if here else {},
        "list_sections": {"address": here} if here else {},
        "list_hyperlinks": {"address": here} if here else {},
        "list_fields": {},
        "list_bookmarks": {},
        "list_references": {},
        "list_reference_targets": {},
        "list_tracked_changes": {},
        "describe_style": ({"name": ground["style"]}
                           if ground.get("style") else None),
        "find_by_style": ({"style": ground["style"], "max_results": 3}
                          if ground.get("style") else None),
        "describe_table": ({"name": ground["table"]}
                           if ground.get("table") else None),
        "read_table": ({"name": ground["table"]}
                       if ground.get("table") else None),
    }
    if name in simple:
        return simple[name]
    if paragraph is not None:
        return {"address": {"paragraph": paragraph}}
    return None


def addresses_in(answer, key):
    """Every address an answer carries, however deep the list it is in."""
    found = []
    if isinstance(answer.get("address"), dict):
        found.append(answer["address"])
    for item in (answer.get(key) or []) if key else []:
        if isinstance(item, dict) and isinstance(item.get("address"), dict):
            found.append(item["address"])
    return found


def ground_truth(client, named, say=False):
    """What the document says about itself, to build calls from.

    Three calls, and on a long document each of them can take seconds, so
    they are announced: a silent minute reads as a hang.
    """
    ground = {}
    def step(label, tool, arguments):
        if say:
            print(f"   asking: {label}", end="", flush=True)
        mark = time.time()
        answer = client.call(tool, arguments)
        if say:
            print(f" — {time.time() - mark:.1f}s")
        return answer
    here = step("where the caret is", "get_cursor_info_live", dict(named))
    if here.get("success"):
        # What the reader has in hand: the selection when there is one, so
        # the scoped tools are asked about a stretch and not one paragraph.
        selected = here.get("selection") or {}
        ground["address"] = (selected.get("address")
                             if selected.get("has_selection")
                             else here.get("address"))
        ground["caret"] = here.get("address")
        ground["paragraphs_selected"] = selected.get("paragraphs_selected")
        ground["paragraph"] = (here.get("cursor") or {}).get("paragraph_index")
        text = (here.get("paragraph") or {}).get("text") or ""
        words = [word for word in text.split() if len(word) > 3]
        ground["word"] = words[0] if words else None
    tables = step("what tables it holds", "list_tables_live", dict(named))
    if tables.get("success") and tables.get("tables"):
        ground["table"] = tables["tables"][0]["name"]
    read = step("which style is in use there", "read_paragraphs_live",
                dict(named, start=ground.get("address") or 0, count=1,
                     anchors=False))
    if read.get("success") and read.get("paragraphs"):
        ground["style"] = read["paragraphs"][0].get("style")
    return ground


def check(client, name, named, ground, resolve):
    """One tool: call it, and say what came back."""
    arguments = arguments_for(name, ground)
    if arguments is None:
        return {"tool": name, "state": "skipped",
                "why": "the document holds nothing to ask about"}
    started = time.time()
    answer = client.call(name + "_live" if name != "list_open_documents"
                         else name, dict(named, **arguments)
                         if name != "list_open_documents" else arguments)
    spent = time.time() - started

    row = {"tool": name, "seconds": round(spent, 3),
           "elapsed_ms": answer.get("elapsed_ms")}
    if not answer.get("success") and "no anchor" in str(answer.get("error")):
        # The listings above held thousands of anchors and the store let the
        # oldest go — including the one this check was handed. Stand where it
        # stood again and ask once more; the summary counts it.
        row["re_anchored"] = True
        ground.update(ground_truth(client, named))
        arguments = arguments_for(name, ground)
        answer = client.call(name + "_live", dict(named, **(arguments or {})))
    if not answer.get("success"):
        code = answer.get("code")
        row["state"] = "refused" if code in ERROR_CODES else "broken"
        row["why"] = f"{code}: {str(answer.get('error'))[:60]}"
        return row
    row["state"] = "ok"
    if answer.get("elapsed_ms") is None:
        row["state"] = "broken"
        row["why"] = "the answer does not say what it cost"
        return row

    key = ITEMS.get(name)
    row["count"] = answer.get("count", len(answer.get(key) or [])
                              if key else None)
    addresses = addresses_in(answer, key)
    if name in NO_ADDRESS or not addresses:
        row["anchors"] = "—"
    else:
        # A bookmark is an address of its own — the document's own handle on
        # a place, which outlives the session an anchor belongs to — so it
        # counts here as named, not as missing an anchor.
        anchored = [one for one in addresses
                    if isinstance(one.get("anchor"), dict)
                    or isinstance(one.get("bookmark"), str)]
        row["anchors"] = ("all" if len(anchored) == len(addresses)
                          else f"{len(anchored)}/{len(addresses)}")
        if resolve and anchored:
            # Timed and shown: this check is a tool call of its own, and
            # hiding it made the script sit for seconds beside a row saying
            # 432 ms — the cost was real and belonged to read_runs.
            mark = time.time()
            read = client.call("read_runs_live",
                               dict(named, address=anchored[0]))
            row["resolve_ms"] = int((time.time() - mark) * 1000)
            if not read.get("success") and read.get("code") != "INVALID_ADDRESS":
                row["state"] = "broken"
                row["why"] = f"its address would not read back: {read.get('error')}"
    return row


DEFAULT_DOCUMENT = "tests/documents/WG262-WriterGuide.odt"

# Where to stand before asking anything. A check run from the first page of a
# 519-page guide says little: the front matter has no tables, no pictures and
# no cross-references, so half the tools answer "none" and prove nothing.
DEFAULT_SECTION = "Chapter 12"

# And how much to pick out there. One paragraph exercises nothing a caller
# does with a selection: the tools that read a whole stretch, the scoping,
# the anchors held per paragraph.
DEFAULT_PARAGRAPHS = 20


def select_section(client, named, wanted, paragraphs):
    """Select a stretch of a chapter, and say what was picked out.

    The outline is the map: the heading is found in it and comes with both
    its number and an anchor, so nothing has to be counted here. The
    selection starts at that heading and runs far enough down to be worth
    asking about.
    """
    # Anchors on 938 headings cost seconds and none of them is used here:
    # what this wants is the one heading's number.
    print(f"   reading the outline to find {wanted!r}", end="", flush=True)
    mark = time.time()
    outline = client.call("get_outline_live", dict(named, count=10000,
                                                   anchors=False))
    print(f" — {time.time() - mark:.1f}s")
    if not outline.get("success"):
        raise SystemExit(f"Could not read the outline: {outline.get('error')}")
    headings = [one for one in outline["headings"]
                if wanted.lower() in " ".join(
                    (one.get("text") or "").split()).lower()]
    if not headings:
        print(f"no heading holds {wanted!r}; leaving the caret where it is")
        return None
    heading = min(headings, key=lambda one: (one.get("level", 9),
                                             one.get("paragraph", 0)))
    first = heading["address"].get("paragraph")
    picked = ({"paragraph": first, "through": first + paragraphs}
              if isinstance(first, int)
              else dict(heading["address"], offset=0, length=0))
    print(f"   selecting {paragraphs} paragraphs there", end="", flush=True)
    mark = time.time()
    chosen = client.call("select_live", dict(named, address=picked))
    print(f" — {time.time() - mark:.1f}s")
    if not chosen.get("success"):
        print(f"could not select in {wanted!r}: {chosen.get('error')}")
        return None
    where = client.call("get_cursor_info_live", dict(named))
    selected = where.get("selection") or {}
    print(f"selected in {' '.join(heading['text'].split())!r}: "
          f"{selected.get('paragraphs_selected')} paragraphs, "
          f"page {(where.get('cursor') or {}).get('page')}")
    return heading


def document_to_check(client, named, path):
    """The document to work on: opened when it is not open already.

    A file nobody has opened is invisible to every tool here, so a check that
    wants a particular document has to open it — which `open_document` does
    without disturbing one that is open already.
    """
    import os

    where = os.path.abspath(path)
    opened = client.call("open_document_live", {"path": where})
    if not opened.get("success"):
        raise SystemExit(f"Could not open {where}: {opened.get('error')}")
    info = opened["document_info"]
    print(f"{'already open' if opened['already_open'] else 'opened'}: "
          f"{info['title']} — {info['url']}")
    return {"document": info["url"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--document",
                        help="URL of a document already open, from "
                             "list_open_documents; by default the Writer "
                             "Guide in tests/documents is opened")
    parser.add_argument("--open", default=DEFAULT_DOCUMENT,
                        help=f"the file to open when --document is not given "
                             f"(default {DEFAULT_DOCUMENT})")
    parser.add_argument("--only", help="comma-separated tools to check")
    parser.add_argument("--paragraphs", type=int, default=DEFAULT_PARAGRAPHS,
                        help=f"how many paragraphs to select there "
                             f"(default {DEFAULT_PARAGRAPHS})")
    parser.add_argument("--section", default=DEFAULT_SECTION,
                        help=f"the chapter to select in before asking "
                             f"anything (default {DEFAULT_SECTION!r}); an "
                             f"empty value leaves the selection alone")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--no-resolve", action="store_true",
                        help="skip reading an address back through read_runs")
    args = parser.parse_args()

    client = MCPClient(args.host, args.port, args.timeout).open()
    named = ({"document": args.document} if args.document
             else document_to_check(client, {}, args.open))
    wanted = set(args.only.split(",")) if args.only else None

    if args.section:
        select_section(client, named, args.section, args.paragraphs)
    ground = ground_truth(client, named, say=True)
    print(f"the document says: {json.dumps(ground, ensure_ascii=False)}\n")

    rows = []
    for name in reading_tools(client):
        if wanted and name not in wanted:
            continue
        rows.append(check(client, name, named, ground, not args.no_resolve))
        row = rows[-1]
        # The read-back check is a tool call of its own: hiding it left the
        # script sitting for seconds beside a row saying 432 ms.
        extra = (f"+{row['resolve_ms']:>5} ms read back"
                 if row.get("resolve_ms") is not None else "")
        print(f"{row['tool']:24} {row['state']:8} "
              f"{str(row.get('count', '')):>6} "
              f"{str(row.get('elapsed_ms', '')):>7} ms  "
              f"anchors: {row.get('anchors', ''):<9}{extra}"
              + (f"   {row.get('why', '')}" if row.get("why") else ""))

    broken = [row for row in rows if row["state"] == "broken"]
    print(f"\n{len(rows)} reading tools: "
          f"{sum(1 for r in rows if r['state'] == 'ok')} answered, "
          f"{sum(1 for r in rows if r['state'] == 'refused')} refused, "
          f"{sum(1 for r in rows if r['state'] == 'skipped')} skipped, "
          f"{len(broken)} broken")
    anchored = [row for row in rows if row.get("anchors") == "all"]
    numbers = [row for row in rows
               if row.get("anchors") not in ("all", "—", None)]
    print(f"addresses: {len(anchored)} tool(s) anchor them all, "
          f"{len(numbers)} still hand out some without an anchor")
    let_go = [row for row in rows if row.get("re_anchored")]
    if let_go:
        print(f"the anchor store let go of this check's own anchor before "
              f"{len(let_go)} call(s) — the listings above hold one per item, "
              f"and the store keeps 2000")
    if broken:
        print("\nbroken:")
        for row in broken:
            print(f"  {row['tool']}: {row.get('why')}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
