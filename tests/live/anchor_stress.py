#!/usr/bin/env python3
"""Count how often an office dies while a session holds many anchors.

The office aborts with `free(): invalid pointer` — heap corruption inside
LibreOffice, not memory exhaustion — while this server holds hundreds of
anchors on a long document. It is **not** reproducible on demand: measured
by hand, once before the first pass, once after it, once on the fourth, and
a six-pass run that never died. A defect that shows up one time in three
cannot be judged by trying it once, so this counts deaths over many
sessions and prints a rate.

What is already known, each ruled out in its own office (see CLAUDE.md):
holding 12000 cursors, churning 12000 through a store of 200, making and
dropping 12000, keeping 2673 cursors made from search hits, and closing a
document with 938 anchors alive — none of those kills anything.

    /usr/bin/python3 tests/live/anchor_stress.py                 # the default pattern
    /usr/bin/python3 tests/live/anchor_stress.py --sessions 10
    /usr/bin/python3 tests/live/anchor_stress.py --pattern walk  # a control
    /usr/bin/python3 tests/live/anchor_stress.py --document /tmp/other.odt

Each session is a fresh office with a profile of its own, so one death does
not colour the next. A session ends when the office dies or when it has done
`--passes` passes. The exit code is 1 if any session died, which is what a
fix has to turn into 0 — and then into 0 again over more sessions than the
failure rate would survive by luck.

Run it with /usr/bin/python3: the venv has no `uno`.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "plugin", "pythonpath"))

GUIDE = os.path.join(ROOT, "tests", "documents", "WG262-WriterGuide.odt")

# What a pass does. Each holds anchors; they differ in where the places come
# from, which is the one difference that has ever mattered.
PATTERNS = ("outline", "walk", "search")


def parsed():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sessions", type=int, default=6,
                        help="how many offices to run, one after another")
    parser.add_argument("--passes", type=int, default=4,
                        help="how many passes a session makes before it stops")
    parser.add_argument("--pattern", choices=PATTERNS, default="outline",
                        help="where the anchored places come from")
    parser.add_argument("--document", default=GUIDE,
                        help="the document to work on; it is copied first, "
                             "since a file open elsewhere cannot be loaded")
    parser.add_argument("--port", type=int, default=2400,
                        help="the first UNO port to use; each session takes "
                             "the next one")
    parser.add_argument("--reopen", action="store_true",
                        help="close and reopen the document between passes, "
                             "which is where the office was seen to die")
    parser.add_argument("--keep", action="store_true",
                        help="keep the copies and profiles for inspection")
    return parser.parse_args()


def start_office(port, profile):
    """A headless office of its own, so one death does not reach the next."""
    return subprocess.Popen(
        ["soffice", f"-env:UserInstallation=file://{profile}",
         "--headless", "--norestore", "--nologo", "--nodefault",
         f"--accept=socket,host=127.0.0.1,port={port};urp;"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def connect(port, seconds=90):
    import uno
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    url = (f"uno:socket,host=127.0.0.1,port={port};urp;"
           "StarOffice.ComponentContext")
    for _ in range(seconds):
        try:
            return resolver.resolve(url)
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"no office answered on port {port}")


def open_document(desktop, path):
    import uno
    from com.sun.star.beans import PropertyValue
    hidden = PropertyValue()
    hidden.Name, hidden.Value = "Hidden", True
    from pathlib import Path
    return desktop.loadComponentFromURL(Path(path).as_uri(), "_blank", 0,
                                        (hidden,))


def places(bridge, doc, pattern):
    """The ranges a pass will anchor, by the route the pattern names."""
    if pattern == "walk":
        return [paragraph for paragraph, _index
                in bridge._body_paragraphs(doc)][:1000]
    if pattern == "search":
        found = bridge._search_style(doc, "Heading 1")
        return [found.getByIndex(index) for index in range(found.getCount())]
    plan = bridge._outline_by_styles(doc, bridge._writer_outline_count(doc))
    if plan is None:                       # the styles account for nothing
        return []
    stream, _total = plan
    return [one["range"] for one in stream]


def one_session(number, args, profile, port):
    """(passes done, how it ended, what the office said)

    A copy of the document per session, because an office that dies leaves
    its lock file behind (`.~lock.<name>#`) and the next session would not be
    able to open the document at all — which is how a first run reported one
    death and five documents that would not open.
    """
    mine = os.path.join(os.path.dirname(args.document),
                        f"session-{number}-{os.path.basename(args.document)}")
    shutil.copy(args.document, mine)
    office = start_office(port, profile)
    doc = None
    try:
        ctx = connect(port)
        desktop = ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", ctx)
        doc = open_document(desktop, mine)
        if doc is None:
            return 0, "the document would not open", ""
        from uno_bridge import UNOBridge
        bridge = UNOBridge.__new__(UNOBridge)
        bridge.ctx, bridge.smgr, bridge.desktop = ctx, ctx.ServiceManager, desktop

        done = 0
        for step in range(1, args.passes + 1):
            began = time.time()
            try:
                found = places(bridge, doc, args.pattern)
                held = 0
                for one in found:
                    paragraph = bridge._paragraph_from(one)
                    if paragraph is not None and bridge._hold_paragraph_anchor(
                            doc, paragraph, None):
                        held += 1
                    if office.poll() is not None:
                        break
            except Exception as trouble:
                return done, f"pass {step} threw {type(trouble).__name__}", ""
            if office.poll() is not None:
                return done, f"the office died during pass {step}", ""
            done = step
            print(f"   session {number}, pass {step}: {held} anchors held, "
                  f"{len(bridge._anchor_store())} in the store, "
                  f"{time.time() - began:.1f}s", flush=True)
            if args.reopen:
                # Closing is where it was seen to die: the anchors of a
                # document that is going away are released then, and the
                # documentation says a user of an object must let its
                # references go when it is disposed.
                try:
                    doc.close(False)
                except Exception as trouble:
                    return done, (f"closing after pass {step} threw "
                                  f"{type(trouble).__name__}"), ""
                time.sleep(1)
                if office.poll() is not None:
                    return done, f"the office died closing after pass {step}", ""
                doc = open_document(desktop, mine)
                if doc is None:
                    return done, f"the document would not reopen after {step}", ""
        # The closing of the last document counts too, and used to be missed:
        # the harness returned "finished" and only then closed.
        ending = "finished"
        if doc is not None:
            try:
                doc.close(False)
            except Exception as trouble:
                ending = f"closing at the end threw {type(trouble).__name__}"
            doc = None
            time.sleep(1)
            if office.poll() is not None:
                ending = "the office died closing at the end"
        return done, ending, ""
    finally:
        if doc is not None and office.poll() is None:
            try:
                doc.close(False)
            except Exception:
                pass
        if office.poll() is None:
            office.terminate()
            try:
                office.wait(timeout=20)
            except Exception:
                office.kill()


def said_by(office):
    """The first line of the office's complaint, which names the fault."""
    try:
        out = office.stderr.read().decode(errors="replace")
    except Exception:
        return ""
    for line in out.splitlines():
        if line and "javaldx" not in line:
            return line.strip()
    return ""


def main():
    args = parsed()
    if not os.path.exists(args.document):
        raise SystemExit(f"no document at {args.document}")
    work = tempfile.mkdtemp(prefix="anchor-stress-")
    copy = os.path.join(work, os.path.basename(args.document))
    shutil.copy(args.document, copy)
    args.document = copy
    print(f"{args.sessions} sessions of up to {args.passes} passes, "
          f"pattern {args.pattern!r}, on {os.path.basename(copy)}")

    deaths, passes = 0, 0
    for number in range(1, args.sessions + 1):
        profile = os.path.join(work, f"profile-{number}")
        port = args.port + number
        done, ending, _ = one_session(number, args, profile, port)
        passes += done
        died = ending.startswith("the office died") or "threw" in ending \
            or "would not reopen" in ending
        deaths += 1 if died else 0
        print(f"session {number}: {done} passes, {ending}", flush=True)

    print(f"\n{deaths} of {args.sessions} sessions died; "
          f"{passes} passes survived in all")
    if deaths:
        print("A death is heap corruption inside LibreOffice — `free(): "
              "invalid pointer` — not anything this server can catch. What a "
              "fix has to do is make this number nought, over more sessions "
              "than luck would carry.")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if deaths else 0


if __name__ == "__main__":
    sys.exit(main())
