"""Check the Writer tools against a real LibreOffice.

Runs its own headless instance with a separate user profile, so a developer's
session is untouched. Must run under a Python that carries the uno bindings;
the repo venv has none. On Linux/macOS that is the system Python
(python3-uno); on Windows it is LibreOffice's own bundled interpreter:

    /usr/bin/python3 tests/live/writer_tools_check.py                              # Linux/macOS
    "C:\\Program Files\\LibreOffice\\program\\python.exe" tests\\live\\writer_tools_check.py   # Windows

The fakes in tests/fake_writer.py encode assumptions about UNO. This checks
them. Anything unverified here is not known to work.
"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    # The output is Cyrillic and the odd StarMath bracket (U+27E6/7); a
    # Windows console's legacy codepage cannot encode either and raises
    # instead of printing.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "plugin", "pythonpath"))

import uno  # noqa: E402
from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK  # noqa: E402

PORT = 2010
FIFTH = "{ frac { 1 } { 5 } }"
TMP = tempfile.gettempdir()
PROFILE = os.path.join(TMP, "mcp_live_check_profile")
failures = []


def tmp_path(name):
    return os.path.join(TMP, name)


def file_uri(path):
    """A file:// URL built the way that survives a Windows drive letter."""
    return Path(path).as_uri()


def check(label, actual, expected):
    ok = actual == expected
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {actual!r}"
          + ("" if ok else f" (expected {expected!r})"))
    if not ok:
        failures.append(label)


def write_test_png(path):
    """A real 8x8 PNG, so the check does not depend on a file lying around."""
    import struct
    import zlib

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))

    rows = []
    for y in range(8):
        row = bytearray(b"\x00")
        for x in range(8):
            row += bytes([(x * 30) % 256, (y * 30) % 256, 200])
        rows.append(bytes(row))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows)))
           + chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(png)
    return path


def connect():
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local)
    url = (f"uno:socket,host=127.0.0.1,port={PORT};urp;"
           "StarOffice.ComponentContext")
    for _ in range(60):
        try:
            return resolver.resolve(url)
        except Exception:
            time.sleep(1)
    raise RuntimeError("could not connect to headless soffice")


def build_document(desktop):
    """Headings, body text and a table, so every code path is exercised."""
    doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, ())
    text = doc.getText()
    cursor = text.createTextCursor()
    for style, body in [
        ("Heading 1", "Chapter One"),
        ("Standard", "Alpha beta alpha."),
        ("Heading 2", "Section A"),
        ("Standard", "Gamma delta."),
    ]:
        cursor.ParaStyleName = style
        text.insertString(cursor, body, False)
        text.insertControlCharacter(cursor, PARAGRAPH_BREAK, False)

    table = doc.createInstance("com.sun.star.text.TextTable")
    table.initialize(2, 2)
    text.insertTextContent(cursor, table, False)
    table.getCellByName("A1").setString("in cell")
    return doc


def _refused(bridge, doc, address):
    """True when an address is refused rather than resolved."""
    try:
        bridge._resolve_address(doc, address)
        return False
    except Exception:
        return True


def force_en_us_locale(ctx):
    """Make this headless profile behave like an English install.

    The checks assume an en-US install throughout (default document
    language, "Table1", "Table of Contents1"...). A LibreOffice whose UI
    language is something else — ru-RU here — creates documents in that
    locale and names things in it instead, which is a property of the
    install, not of the tools under test. Setting these two config nodes
    on this run's own throwaway profile pins it to en-US regardless of
    the host machine's LibreOffice language, without touching anything
    the developer's own LibreOffice would ever load.
    """
    from com.sun.star.beans import PropertyValue

    provider = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.configuration.ConfigurationProvider", ctx)

    def _set(nodepath, name, value):
        arg = PropertyValue()
        arg.Name, arg.Value = "nodepath", nodepath
        access = provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationUpdateAccess", (arg,))
        access.setPropertyValue(name, value)
        access.commitChanges()

    _set("/org.openoffice.Setup/L10N", "ooLocale", "en-US")
    _set("/org.openoffice.Office.Linguistic/General", "DefaultLocale", "en-US")


soffice = subprocess.Popen([
    "soffice", f"-env:UserInstallation={file_uri(PROFILE)}",
    "--headless", "--norestore", "--nologo", "--nodefault",
    f"--accept=socket,host=127.0.0.1,port={PORT};urp;",
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    ctx = connect()
    force_en_us_locale(ctx)
    desktop = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", ctx)
    doc = build_document(desktop)

    from uno_bridge import UNOBridge
    bridge = UNOBridge.__new__(UNOBridge)  # no local desktop wanted
    bridge.ctx = ctx
    bridge.smgr = ctx.ServiceManager
    bridge.desktop = desktop        # for the tools that find a document themselves

    print("--- get_outline ---")
    outline = bridge.get_outline(doc=doc)
    print(outline)
    check("outline success", outline.get("success"), True)
    check("heading texts", [h["text"] for h in outline["headings"]],
          ["Chapter One", "Section A"])
    check("heading levels", [h["level"] for h in outline["headings"]], [1, 2])
    check("heading paragraphs", [h["paragraph"] for h in outline["headings"]], [0, 2])
    check("every heading is an address to work from",
          all(h["address"].get("anchor", {}).get("type") == "paragraph"
              for h in outline["headings"]), True)
    # A long document has more headings than one call carries, and the rest
    # used to be unreachable: the outline stopped and said only "truncated".
    one_at_a_time = bridge.get_outline(count=1, doc=doc)
    check("an outline can be asked for a page at a time",
          ([h["text"] for h in one_at_a_time["headings"]],
           one_at_a_time["total_headings"], one_at_a_time["more"]),
          (["Chapter One"], 2, True))
    check("and a count past the default carries the whole map",
          ([h["text"] for h in bridge.get_outline(count=5000, anchors=False,
                                                  doc=doc)["headings"]],
           bridge.get_outline(count=5000, anchors=False,
                              doc=doc)["truncated"]),
          (["Chapter One", "Section A"], False))
    check("and goes on from the last heading's own address",
          [h["text"] for h in bridge.get_outline(
              start=one_at_a_time["headings"][-1]["address"], count=2,
              doc=doc)["headings"]],
          ["Chapter One", "Section A"])

    print("\n--- read_paragraphs ---")
    window = bridge.read_paragraphs(start=1, count=2, doc=doc)
    print(window)
    check("window texts", [p["text"] for p in window["paragraphs"]],
          ["Alpha beta alpha.", "Section A"])
    check("window indices", [p["paragraph"] for p in window["paragraphs"]], [1, 2])
    check("style of a heading", window["paragraphs"][1]["style"], "Heading 2")

    # Paging by number is the one place the anchor habit used to break: the
    # reader hands out addresses and then would only take a number back.
    handed_out = window["paragraphs"][0]["address"]
    check("a read starts at an address it handed out",
          [p["text"] for p in bridge.read_paragraphs(
              start=handed_out, count=1, doc=doc)["paragraphs"]],
          ["Alpha beta alpha."])
    check("and a block address says its own length",
          [p["text"] for p in bridge.read_paragraphs(
              start={"paragraph": 1, "through": 2}, doc=doc)["paragraphs"]],
          ["Alpha beta alpha.", "Section A"])
    text = doc.getText()
    above = text.createTextCursorByRange(text.getStart())
    text.insertString(above, "Put in above.", False)
    text.insertControlCharacter(above, PARAGRAPH_BREAK, False)
    moved = bridge.read_paragraphs(start=handed_out, count=1, doc=doc)
    check("an address still reads its own paragraph after the numbers moved",
          ([p["text"] for p in moved["paragraphs"]], moved["start"]),
          (["Alpha beta alpha."], 2))
    check("where the number it was handed out with now reads another",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "Chapter One")
    text.removeTextContent(bridge._paragraph_at(text, 0))
    check("the document is as it was", bridge.read_paragraphs(
        start=0, count=1, doc=doc)["paragraphs"][0]["text"], "Chapter One")

    whole = bridge.read_paragraphs(start=0, count=5000, anchors=False, doc=doc)
    check("a read can be asked for the whole document at once",
          (whole["count"], whole["count"] == whole["total_paragraphs"],
           whole["anchors"]),
          (whole["total_paragraphs"], True, False))

    print("\n--- find_text ---")
    found = bridge.find_text("alpha", doc=doc)
    print(found)
    check("hit count", found.get("total_hits"), 2)
    first = dict(found["hits"][0]["address"])
    # A hit is handed out with the anchor that holds it, so that passing the
    # address back hits the same words however the paragraphs above it move.
    check("first hit is anchored",
          (first.pop("anchor", {}) or {}).get("type"), "text")
    check("first hit address", first, {"paragraph": 1, "offset": 0,
                                       "length": 5})
    check("and the anchor resolves to the hit",
          bridge._resolve_address(
              doc, found["hits"][0]["address"]).getString(), "Alpha")
    check("hit context", found["hits"][0]["context"], "Alpha beta alpha.")

    print("\n--- find_text with a regular expression ---")
    regex_hits = bridge.find_text("g[a-z]+a", regex=True, doc=doc)
    check("regex matched", [h["matched"] for h in regex_hits["hits"]], ["Gamma"])

    print("\n--- address round trip: every hit resolves to what was found ---")
    for hit in bridge.find_text("alpha", doc=doc)["hits"]:
        resolved = bridge._resolve_address(doc, hit["address"])
        check(f"round trip {hit['address']}", resolved.getString(), hit["matched"])

    print("\n--- resolver against the whole paragraph and a slice ---")
    check("whole paragraph",
          bridge._resolve_address(doc, {"paragraph": 3}).getString(), "Gamma delta.")
    check("slice",
          bridge._resolve_address(
              doc, {"paragraph": 1, "offset": 6, "length": 4}).getString(), "beta")

    print("\n--- resolver rejects what it cannot address ---")
    from uno_bridge import AddressError
    for label, address in [("paragraph past the end", {"paragraph": 99}),
                           ("offset past the end", {"paragraph": 3, "offset": 500}),
                           ("neither key", {"offset": 1})]:
        try:
            bridge._resolve_address(doc, address)
            check(label, "no error", "AddressError")
        except AddressError as e:
            print(f"PASS  {label}: {e}")

    print("\n--- a table does not break paragraph numbering ---")
    window = bridge.read_paragraphs(doc=doc)
    print("paragraphs:", [(p["paragraph"], p["text"]) for p in window["paragraphs"]])
    # Four inserted paragraphs, plus whatever empty ones Writer leaves around the
    # table; the point is that the table itself is not counted as a paragraph.
    check("at least the four inserted paragraphs",
          window["total_paragraphs"] >= 4, True)
    check("no paragraph holds the table's text",
          any(p["text"] == "in cell" for p in window["paragraphs"]), False)

    print("\n--- replace_selection: the selection is rewritten, not added to ---")
    body = doc.getText()
    paragraphs = []
    enumeration = body.createEnumeration()
    while enumeration.hasMoreElements():
        element = enumeration.nextElement()
        if hasattr(element, "getStart"):
            paragraphs.append(element)
    target = paragraphs[1]              # "Alpha beta alpha."
    before = target.getString()
    selection = body.createTextCursorByRange(target.getStart())
    selection.goRight(5, True)          # "Alpha"
    doc.getCurrentController().select(selection)

    result = bridge.replace_selection("First", doc=doc)
    print(result)
    check("replace succeeded", result.get("success"), True)
    check("paragraph rewritten", target.getString(), "First beta alpha.")
    check("original text is gone", "Alpha beta" in target.getString(), False)
    check("reported paragraph", result.get("paragraph"), 1)
    check("tracked", result.get("tracked"), False)

    print("\n--- the whole edit is one undo step ---")
    undo = doc.UndoManager
    check("undo entry title", undo.getAllUndoActionTitles()[0],
          "MCP: replace selection")
    undo.undo()
    check("one undo restores the original", target.getString(), before)

    print("\n--- nothing selected: refused, document untouched ---")
    collapsed = body.createTextCursorByRange(target.getStart())
    doc.getCurrentController().select(collapsed)
    refused = bridge.replace_selection("should not appear", doc=doc)
    print(refused)
    check("refused", refused.get("success"), False)
    check("paragraph untouched", target.getString(), before)

    print("\n--- track_changes=True keeps the original, struck through ---")
    selection = body.createTextCursorByRange(target.getStart())
    selection.goRight(5, True)
    doc.getCurrentController().select(selection)
    tracked = bridge.replace_selection("Second", track_changes=True, doc=doc)
    print(tracked)
    check("tracked flag", tracked.get("tracked"), True)
    check("new text present", "Second" in target.getString(), True)
    check("original still there as a tracked deletion",
          "Alpha" in target.getString(), True)
    check("redlines recorded", doc.getRedlines().getCount() > 0, True)
    check("document's own RecordChanges restored", doc.RecordChanges, False)
    undo.undo()

    print("\n--- replace_range by address: translating a heading ---")
    outline_before = bridge.get_outline(doc=doc)
    heading = outline_before["headings"][1]        # "Section A" at its index
    print("heading to rewrite:", heading)
    replaced = bridge.replace_range({"paragraph": heading["paragraph"]},
                                    "Section Two", doc=doc)
    print(replaced)
    check("replace_range succeeded", replaced.get("success"), True)

    outline_after = bridge.get_outline(doc=doc)
    check("heading text is translated",
          [h["text"] for h in outline_after["headings"]],
          ["Chapter One", "Section Two"])
    check("heading is still a heading at the same level",
          outline_after["headings"][1]["level"], heading["level"])
    check("heading is still at the same paragraph",
          outline_after["headings"][1]["paragraph"], heading["paragraph"])
    check("paragraph count unchanged",
          outline_after["total_paragraphs"], outline_before["total_paragraphs"])

    print("\n--- replace_range on part of a paragraph ---")
    body_paragraph = 3                              # "Gamma delta."
    part = bridge.replace_range(
        {"paragraph": body_paragraph, "offset": 0, "length": 5}, "GAMMA", doc=doc)
    check("partial replace succeeded", part.get("success"), True)
    check("only the addressed part changed",
          bridge.read_paragraphs(start=body_paragraph, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "GAMMA delta.")

    print("\n--- a search hit's address can be rewritten straight away ---")
    hit = bridge.find_text("beta", doc=doc)["hits"][0]
    rewritten = bridge.replace_range(hit["address"], "BETA", doc=doc)
    check("hit rewritten", rewritten.get("success"), True)
    check("text now holds the replacement",
          "BETA" in bridge.read_paragraphs(
              start=hit["address"]["paragraph"], count=1,
              doc=doc)["paragraphs"][0]["text"], True)

    print("\n--- replace_range refuses an address it cannot resolve ---")
    bad = bridge.replace_range({"paragraph": 99}, "nowhere", doc=doc)
    check("bad address refused", bad.get("success"), False)

    print("\n--- language: a translation must not inherit the original's locale ---")
    speller = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.linguistic2.SpellChecker", ctx)

    def locale_of(paragraph_index):
        window = bridge.read_paragraphs(start=paragraph_index, count=1, doc=doc)
        del window  # only to assert the paragraph exists
        paragraph = bridge._paragraph_at(doc.getText(), paragraph_index)
        portion = paragraph.createEnumeration().nextElement()
        return f"{portion.CharLocale.Language}-{portion.CharLocale.Country}"

    replaced = bridge.replace_range({"paragraph": 0}, "Схемы и типы", doc=doc)
    check("replaced without a language", replaced.get("language"), None)
    check("locale unchanged, so Russian is checked as English",
          locale_of(0), "en-US")
    russian_as_english = bridge._paragraph_at(doc.getText(), 0).createEnumeration(
        ).nextElement().CharLocale
    check("'Схемы' is called a misspelling under that locale",
          speller.isValid("Схемы", russian_as_english, ()), False)

    tagged = bridge.replace_range({"paragraph": 0}, "Схемы и типы",
                                  language="ru-RU", doc=doc)
    check("replaced with a language", tagged.get("language"), "ru-RU")
    check("locale now Russian", locale_of(0), "ru-RU")
    russian_locale = bridge._paragraph_at(doc.getText(), 0).createEnumeration(
        ).nextElement().CharLocale
    check("'Схемы' is now spelled correctly",
          speller.isValid("Схемы", russian_locale, ()), True)
    check("'Схеммы' is still a misspelling",
          speller.isValid("Схеммы", russian_locale, ()), False)

    print("\n--- set_language fixes text already written ---")
    bridge.replace_range({"paragraph": 3}, "Гамма дельта.", doc=doc)
    check("wrong locale before", locale_of(3), "en-US")
    fixed = bridge.set_language({"paragraph": 3}, "ru-RU", doc=doc)
    print(fixed)
    check("set_language succeeded", fixed.get("success"), True)
    check("locale after", locale_of(3), "ru-RU")

    print("\n--- a language that is not a tag is refused ---")
    bad_language = bridge.set_language({"paragraph": 3}, "russian please", doc=doc)
    check("refused", bad_language.get("success"), False)

    print("\n--- check_spelling against real dictionaries ---")
    bridge.replace_range({"paragraph": 3}, "Схеммы и типы описывают",
                         language="ru-RU", doc=doc)
    report = bridge.check_spelling(address={"paragraph": 3}, doc=doc)
    print(report)
    check("spell check succeeded", report.get("success"), True)
    check("the misspelling is found",
          [hit["word"] for hit in report["misspelled"]], ["Схеммы"])
    check("suggestions offered",
          "Схемы" in report["misspelled"][0]["suggestions"], True)
    check("reported language", report["misspelled"][0]["language"], "ru-RU")

    print("\n--- the reported address resolves to the misspelled word ---")
    hit = report["misspelled"][0]
    check("address resolves to the word",
          bridge._resolve_address(doc, hit["address"]).getString(), hit["word"])

    print("\n--- and the fix goes back through replace_range ---")
    bridge.replace_range(hit["address"], hit["suggestions"][0],
                         language="ru-RU", doc=doc)
    after = bridge.check_spelling(address={"paragraph": 3}, doc=doc)
    check("nothing misspelled after the fix", after["misspelled"], [])
    check("paragraph now reads correctly",
          bridge.read_paragraphs(start=3, count=1, doc=doc)["paragraphs"][0]["text"],
          "Схемы и типы описывают")

    print("\n--- correct Russian marked as English is reported, and why ---")
    bridge.replace_range({"paragraph": 3}, "Схемы и типы", doc=doc)
    bridge.set_language({"paragraph": 3}, "en-US", doc=doc)
    wrong_language = bridge.check_spelling(address={"paragraph": 3}, doc=doc)
    check("correct words reported as misspelled under the wrong language",
          len(wrong_language["misspelled"]) > 0, True)
    check("the report names the language that judged them",
          wrong_language["misspelled"][0]["language"], "en-US")
    bridge.set_language({"paragraph": 3}, "ru-RU", doc=doc)
    check("and they are clean once the language is right",
          bridge.check_spelling(address={"paragraph": 3}, doc=doc)["misspelled"], [])

    print("\n--- get_document_info reports the recording state ---")
    info = bridge.get_document_info(doc)
    check("recording reported off", info.get("track_changes"), False)
    check("no recorded changes yet", info.get("tracked_changes"), 0)

    doc.RecordChanges = True
    check("recording reported on after switching it on",
          bridge.get_document_info(doc).get("track_changes"), True)

    print("\n--- with recording on, the default records the edit ---")
    # Prepare the paragraph while nothing is recorded, so the redline count
    # starts from a known place: an edit on top of an already-recorded edit
    # merges into it and leaves the count unchanged, which measures nothing.
    doc.RecordChanges = False
    bridge.replace_range({"paragraph": 3}, "Gamma delta.", language="en-US", doc=doc)
    doc.RecordChanges = True
    baseline = doc.getRedlines().getCount()

    followed = bridge.replace_range({"paragraph": 3}, "Гамма дельта.",
                                    language="ru-RU", doc=doc)
    print(followed)
    check("reported as tracked", followed.get("tracked"), True)
    check("a change was recorded",
          doc.getRedlines().getCount() > baseline, True)
    recorded_text = bridge.read_paragraphs(
        start=3, count=1, doc=doc)["paragraphs"][0]["text"]
    print("   text now:", repr(recorded_text))
    check("the original is still there, struck through",
          "Gamma delta." in recorded_text, True)

    print("\n--- track_changes=False refuses to record, even here ---")
    doc.UndoManager.undo()            # drop the tracked edit, keep recording on
    baseline = doc.getRedlines().getCount()
    clean = bridge.replace_range({"paragraph": 3}, "Гамма дельта.",
                                 track_changes=False, language="ru-RU", doc=doc)
    print(clean)
    check("reported as not tracked", clean.get("tracked"), False)
    check("nothing new was recorded", doc.getRedlines().getCount(), baseline)
    clean_text = bridge.read_paragraphs(
        start=3, count=1, doc=doc)["paragraphs"][0]["text"]
    check("only the replacement remains", clean_text, "Гамма дельта.")
    check("the document keeps its own setting", doc.RecordChanges, True)

    print("\n--- list_styles / apply_paragraph_style / format_range ---")
    doc.RecordChanges = False
    styles = bridge.list_styles(doc=doc)
    check("styles listed", styles.get("success"), True)
    check("Preformatted Text is among them",
          "Preformatted Text" in styles["styles"], True)
    print(f"   {styles['count']} paragraph styles")

    bridge.replace_range({"paragraph": 3}, "query { hero { name } }",
                         language="en-US", doc=doc)
    styled = bridge.apply_paragraph_style({"paragraph": 3}, "Preformatted Text",
                                          doc=doc)
    print(styled)
    check("style applied", styled.get("success"), True)
    check("the paragraph now carries it",
          bridge.read_paragraphs(start=3, count=1,
                                 doc=doc)["paragraphs"][0]["style"],
          "Preformatted Text")
    paragraph = bridge._paragraph_at(doc.getText(), 3)
    font = paragraph.createEnumeration().nextElement().CharFontName
    print("   font from the style:", font)
    check("the style brought a monospace font", "Mono" in font, True)

    refused = bridge.apply_paragraph_style({"paragraph": 3}, "No Such Style",
                                           doc=doc)
    check("an unknown style is refused", refused.get("success"), False)
    check("and the caller is pointed at list_styles",
          "list_styles" in refused["error"], True)

    formatted = bridge.format_range({"paragraph": 3, "offset": 0, "length": 5},
                                    bold=True, font_name="Liberation Mono",
                                    doc=doc)
    print(formatted)
    check("formatting applied", formatted.get("success"), True)
    first = bridge._paragraph_at(doc.getText(), 3).createEnumeration().nextElement()
    check("the first run is bold", first.CharWeight, 150.0)
    check("and monospace", first.CharFontName, "Liberation Mono")

    check("formatting with nothing to apply is refused",
          bridge.format_range({"paragraph": 3}, doc=doc).get("success"), False)

    print("\n--- colours and a framed block ---")
    bridge.replace_range({"paragraph": 3}, "query { hero }", language="en-US",
                         doc=doc)
    coloured = bridge.format_range({"paragraph": 3, "offset": 0, "length": 5},
                                   color="#0000CC", background_color="#FFFFCC",
                                   doc=doc)
    print(coloured)
    check("colour applied", coloured.get("success"), True)
    run = bridge._paragraph_at(doc.getText(), 3).createEnumeration().nextElement()
    check("character colour", hex(run.CharColor), hex(0x0000CC))
    check("character background", hex(run.CharBackColor), hex(0xFFFFCC))

    framed = bridge.format_paragraph({"paragraph": 3}, background_color="#F5F5F5",
                                     border=True, border_color="#808080",
                                     border_width=0.5, padding=2.0, doc=doc)
    print(framed)
    check("frame applied", framed.get("success"), True)
    paragraph = bridge._paragraph_at(doc.getText(), 3)
    check("top border colour", hex(paragraph.TopBorder.Color), hex(0x808080))
    check("border on all four sides",
          [paragraph.TopBorder.LineWidth > 0, paragraph.BottomBorder.LineWidth > 0,
           paragraph.LeftBorder.LineWidth > 0, paragraph.RightBorder.LineWidth > 0],
          [True, True, True, True])
    check("padding on all four sides",
          [paragraph.TopBorderDistance, paragraph.BottomBorderDistance,
           paragraph.LeftBorderDistance, paragraph.RightBorderDistance],
          [71, 71, 71, 71])
    check("background really applied, not silently ignored",
          hex(paragraph.ParaBackColor & 0xFFFFFF), hex(0xF5F5F5))
    check("and the paragraph is no longer transparent",
          paragraph.ParaBackTransparent, False)
    check("consecutive paragraphs would merge into one box",
          paragraph.ParaIsConnectBorder, True)

    removed = bridge.format_paragraph({"paragraph": 3}, border=False, doc=doc)
    check("border removed", removed.get("success"), True)
    check("zero width now",
          bridge._paragraph_at(doc.getText(), 3).TopBorder.LineWidth, 0)

    check("a colour that is not one is refused",
          bridge.format_range({"paragraph": 3}, color="blueish",
                              doc=doc).get("success"), False)

    print("\n--- runs: translating without losing the formatting ---")
    # A paragraph made of four differently formatted runs, like real prose with
    # an inline code term and a coloured phrase in it.
    body = doc.getText()
    paragraph = bridge._paragraph_at(body, 3)
    span = body.createTextCursorByRange(paragraph.getStart())
    span.gotoEndOfParagraph(True)
    span.setString("")
    cursor = body.createTextCursorByRange(paragraph.getStart())
    for piece, font, colour in [("Character", "Liberation Mono", -1),
                                (" is a ", "Liberation Serif", -1),
                                ("GraphQL Object type", "Liberation Serif", 0x000080),
                                (", meaning it has fields.", "Liberation Serif", -1)]:
        cursor.CharFontName = font
        cursor.CharColor = colour
        body.insertString(cursor, piece, False)

    read = bridge.read_runs({"paragraph": 3}, doc=doc)
    print(read)
    check("four runs read", read.get("count"), 4)
    check("run texts", [r["text"] for r in read["runs"]],
          ["Character", " is a ", "GraphQL Object type", ", meaning it has fields."])
    check("the monospace run is reported as such",
          read["runs"][0]["font_name"], "Liberation Mono")
    check("the coloured run is reported as such",
          read["runs"][2]["color"], "#000080")
    check("an automatic colour is reported as no colour",
          read["runs"][1]["color"], None)

    print("\n--- every run's address resolves to that run ---")
    for run in read["runs"]:
        check(f"address of {run['text'][:14]!r}",
              bridge._resolve_address(doc, run["address"]).getString(), run["text"])

    print("\n--- what plain replacement does, for comparison ---")
    flattened = body.createTextCursorByRange(paragraph.getStart())
    flattened.gotoEndOfParagraph(True)
    flattened.setString("Character — это объектный тип GraphQL, то есть тип с полями.")
    after_plain = bridge.read_runs({"paragraph": 3}, doc=doc)
    check("plain setString collapses it to one run", after_plain["count"], 1)
    check("and the monospace font is gone",
          after_plain["runs"][0]["font_name"] != "Liberation Mono", True)

    print("\n--- replace_runs keeps every run's look ---")
    written = bridge.replace_runs({"paragraph": 3}, [
        {"text": "Character", "font_name": "Liberation Mono", "language": "en-US"},
        {"text": " — это ", "language": "ru-RU"},
        {"text": "объектный тип GraphQL", "color": "#000080", "language": "ru-RU"},
        {"text": ", то есть тип с полями.", "language": "ru-RU"},
    ], doc=doc)
    print(written)
    check("written", written.get("success"), True)
    check("four runs written", written.get("runs"), 4)

    result = bridge.read_runs({"paragraph": 3}, doc=doc)
    print([(r["text"][:16], r["font_name"], r["color"], r["language"])
           for r in result["runs"]])
    check("still four runs", result["count"], 4)
    check("the term stayed monospace", result["runs"][0]["font_name"],
          "Liberation Mono")
    check("the phrase stayed navy", result["runs"][2]["color"], "#000080")
    check("and the translation is marked Russian",
          result["runs"][2]["language"], "ru-RU")
    check("while the term stayed English", result["runs"][0]["language"], "en-US")
    check("one undo step for the whole thing",
          doc.UndoManager.getAllUndoActionTitles()[0], "MCP: replace runs")

    print("\n--- a hyperlink survives a rewrite only if it is carried over ---")
    body = doc.getText()
    paragraph = bridge._paragraph_at(body, 3)
    span = body.createTextCursorByRange(paragraph.getStart())
    span.gotoEndOfParagraph(True)
    span.setString("")
    cursor = body.createTextCursorByRange(paragraph.getStart())
    body.insertString(cursor, "See ", False)
    linked = body.createTextCursorByRange(paragraph.getStart())
    linked.goRight(4, False)
    body.insertString(linked, "the schema docs", False)
    linked = body.createTextCursorByRange(paragraph.getStart())
    linked.goRight(4, False)
    linked.goRight(15, True)
    linked.HyperLinkURL = "https://graphql.org/learn/schema/"
    linked.HyperLinkTarget = "_blank"
    linked.UnvisitedCharStyleName = "Internet link"
    linked.VisitedCharStyleName = "Visited Internet Link"

    read = bridge.read_runs({"paragraph": 3}, doc=doc)
    print([(r["text"], r["link"]) for r in read["runs"]])
    linked_run = next(r for r in read["runs"] if r["link"])
    check("the link is reported", linked_run["link"],
          "https://graphql.org/learn/schema/")
    check("and where it opens", linked_run["link_target"], "_blank")

    print("\n--- dropping it: a plain rewrite destroys the link ---")
    plain = body.createTextCursorByRange(paragraph.getStart())
    plain.gotoEndOfParagraph(True)
    plain.setString("Смотри документацию по схеме")
    check("no link left after a plain replacement",
          any(r["link"] for r in bridge.read_runs({"paragraph": 3},
                                                  doc=doc)["runs"]), False)

    print("\n--- carrying it: replace_runs keeps the link and its look ---")
    written = bridge.replace_runs({"paragraph": 3}, [
        {"text": "Смотри ", "language": "ru-RU"},
        {"text": "документацию по схеме",
         "link": "https://graphql.org/learn/schema/", "link_target": "_blank",
         "language": "ru-RU"},
    ], doc=doc)
    print(written)
    after = bridge.read_runs({"paragraph": 3}, doc=doc)
    print([(r["text"], r["link"], r["color"], r["underline"]) for r in after["runs"]])
    restored = next((r for r in after["runs"] if r["link"]), None)
    check("the link is back", restored is not None, True)
    check("with the right target", restored["link_target"], "_blank")
    check("and it looks like a link (underlined)", restored["underline"], True)
    check("its text is the translation", restored["text"], "документацию по схеме")

    print("\n--- read a run, change only its text, write it back ---")
    runs = bridge.read_runs({"paragraph": 3}, doc=doc)["runs"]
    rewritten = [dict(run, text=run["text"].upper()) for run in runs]
    bridge.replace_runs({"paragraph": 3}, rewritten, doc=doc)
    round_trip = bridge.read_runs({"paragraph": 3}, doc=doc)["runs"]
    check("the text changed", [r["text"] for r in round_trip],
          [r["text"] for r in rewritten])
    check("the link came through the round trip",
          next((r["link"] for r in round_trip if r["link"]), None),
          "https://graphql.org/learn/schema/")

    print("\n--- a flat replacement of formatted text is refused ---")
    body = doc.getText()
    paragraph = bridge._paragraph_at(body, 3)
    span = body.createTextCursorByRange(paragraph.getStart())
    span.gotoEndOfParagraph(True)
    span.setString("")
    cursor = body.createTextCursorByRange(paragraph.getStart())
    body.insertString(cursor, "query is an entry point for reads.", False)
    code = body.createTextCursorByRange(paragraph.getStart())
    code.goRight(5, True)
    code.CharStyleName = "Source Text"
    italic = body.createTextCursorByRange(paragraph.getStart())
    italic.goRight(12, False)
    italic.goRight(11, True)
    italic.CharPosture = uno.Enum("com.sun.star.awt.FontSlant", "ITALIC")

    before = bridge.read_runs({"paragraph": 3}, doc=doc)
    print("runs:", [(r["text"], r["character_style"], r["italic"])
                    for r in before["runs"]])
    check("the paragraph really holds several runs", before["count"] > 1, True)
    check("the italic run is reported as italic",
          [r["italic"] for r in before["runs"]], [False, False, True, False])

    refused = bridge.replace_range({"paragraph": 3}, "translation", doc=doc)
    print(refused)
    check("refused", refused.get("success"), False)
    check("the refusal names the run count",
          str(before["count"]) in refused["error"], True)
    check("and points at the safe route",
          "read_runs" in refused["error"] and "replace_runs" in refused["error"],
          True)
    check("the document is untouched",
          bridge.read_paragraphs(start=3, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "query is an entry point for reads.")

    print("\n--- a single run with a hyperlink is refused too ---")
    linked_paragraph = bridge._paragraph_at(body, 1)
    span = body.createTextCursorByRange(linked_paragraph.getStart())
    span.gotoEndOfParagraph(True)
    span.setString("the schema docs")
    span = body.createTextCursorByRange(linked_paragraph.getStart())
    span.gotoEndOfParagraph(True)
    span.HyperLinkURL = "https://graphql.org/learn/schema/"
    linked_refused = bridge.replace_range({"paragraph": 1}, "документация", doc=doc)
    check("refused because of the link", linked_refused.get("success"), False)
    check("the refusal mentions the link",
          "hyperlink" in linked_refused["error"].lower(), True)

    print("\n--- flatten=true goes ahead and reports the damage ---")
    flattened = bridge.replace_range({"paragraph": 3}, "translation",
                                     language="ru-RU", flatten=True, doc=doc)
    print(flattened)
    check("went ahead", flattened.get("success"), True)
    check("reported the runs it flattened", flattened.get("runs_flattened"),
          before["count"])
    check("one run left afterwards",
          bridge.read_runs({"paragraph": 3}, doc=doc)["count"], 1)

    print("\n--- a uniform paragraph is replaced without ceremony ---")
    plain = bridge.replace_range({"paragraph": 3}, "plain text", doc=doc)
    check("allowed", plain.get("success"), True)
    check("nothing was flattened", plain.get("runs_flattened"), None)

    print("\n--- comments: a comment occupies no characters ---")
    body = doc.getText()
    paragraph = bridge._paragraph_at(body, 3)
    span = body.createTextCursorByRange(paragraph.getStart())
    span.gotoEndOfParagraph(True)
    span.setString("query and mutation are roots")
    for offset, length in ((0, 5), (10, 8)):
        code = body.createTextCursorByRange(paragraph.getStart())
        code.goRight(offset, False)
        code.goRight(length, True)
        code.CharStyleName = "Source Text"

    anchored = bridge.add_comment({"paragraph": 3, "offset": 0, "length": 18},
                                  "A term, left untranslated",
                                  author="Reviewer", doc=doc)
    print(anchored)
    check("the comment was anchored", anchored.get("success"), True)
    check("over the text it is about", anchored.get("anchor_text"),
          "query and mutation")
    check("the text is unchanged",
          bridge.read_paragraphs(start=3, count=1, doc=doc)["paragraphs"][0]["text"],
          "query and mutation are roots")

    listed = bridge.list_comments(doc=doc)
    print(listed)
    check("it is listed once", listed.get("count"), 1)
    check("with its author", listed["comments"][0]["author"], "Reviewer")
    check("with its text", listed["comments"][0]["content"],
          "A term, left untranslated")
    check("and the address of the text it covers",
          listed["comments"][0]["address"],
          {"paragraph": 3, "offset": 0, "length": 18})
    check("listing one paragraph finds it",
          bridge.list_comments({"paragraph": 3}, doc=doc)["count"], 1)
    check("listing another paragraph does not",
          bridge.list_comments({"paragraph": 1}, doc=doc)["count"], 0)

    print("\n--- read_runs reports it on every run its anchor covers ---")
    runs = bridge.read_runs({"paragraph": 3}, doc=doc)["runs"]
    print([(r["text"], len(r["comments"])) for r in runs])
    covered = [r for r in runs if r["comments"]]
    check("the covered runs carry it", [r["text"] for r in covered],
          ["query", " and ", "mutation"])
    check("all reporting the same comment",
          all(r["comments"][0]["content"] == "A term, left untranslated"
              for r in covered), True)
    check("the run past the anchor carries none",
          [r["comments"] for r in runs if r["text"] == " are roots"], [[]])

    print("\n--- a flat replacement is refused, and counts it once ---")
    refused = bridge.replace_range({"paragraph": 3}, "translation", doc=doc)
    print(refused)
    check("refused", refused.get("success"), False)
    check("naming one comment, not one per run",
          "1 comment" in refused["error"], True)
    check("the comment is still there", bridge.list_comments(doc=doc)["count"], 1)

    print("\n--- translating through replace_runs keeps the comment ---")
    translated = [dict(run, text={"query": "query", " and ": " и ",
                                  "mutation": "mutation",
                                  " are roots": " — корневые типы"}[run["text"]],
                       language="ru-RU" if run["text"] in (" and ", " are roots")
                       else None)
                  for run in runs]
    written = bridge.replace_runs({"paragraph": 3}, translated, doc=doc)
    print(written)
    check("written", written.get("success"), True)
    check("the comment was written once", written.get("comments_written"), 1)
    after = bridge.list_comments(doc=doc)
    print(after)
    check("one comment afterwards, not three", after.get("count"), 1)
    check("its text survived", after["comments"][0]["content"],
          "A term, left untranslated")
    check("its author survived", after["comments"][0]["author"], "Reviewer")
    check("and it still covers the translated stretch",
          after["comments"][0]["anchor_text"], "query и mutation")
    check("the monospace runs survived the translation",
          [r["character_style"] for r in
           bridge.read_runs({"paragraph": 3}, doc=doc)["runs"]],
          ["Source Text", None, "Source Text", None])

    print("\n--- rewriting commented text without the comments is refused ---")
    current = bridge.read_runs({"paragraph": 3}, doc=doc)["runs"]
    stripped = [dict(run, comments=[], text=run["text"].upper())
                for run in current]
    dropped = bridge.replace_runs({"paragraph": 3}, stripped, doc=doc)
    print(dropped)
    check("refused", dropped.get("success"), False)
    check("saying what would be lost", "comment" in dropped["error"].lower(), True)
    check("the comment is still there", bridge.list_comments(doc=doc)["count"], 1)

    print("\n--- leaving the commented text alone needs no comments back ---")
    same = [dict(run, comments=[]) for run in current]
    allowed = bridge.replace_runs({"paragraph": 3}, same, doc=doc)
    print(allowed)
    check("allowed, since nothing under a comment changes",
          allowed.get("success"), True)
    check("the comment was kept rather than written again",
          (allowed.get("comments_kept"), allowed.get("comments_written")),
          (1, 0))
    check("and it is still there", bridge.list_comments(doc=doc)["count"], 1)

    print("\n--- flatten=true drops it and says so ---")
    flat = bridge.replace_range({"paragraph": 3}, "translation", language="ru-RU",
                                flatten=True, doc=doc)
    print(flat)
    check("went ahead", flat.get("success"), True)
    check("reported the comment it dropped", flat.get("comments_dropped"), 1)
    check("no comments left", bridge.list_comments(doc=doc)["count"], 0)

    print("\n--- a comment on a point, with no text under it ---")
    point = bridge.add_comment({"paragraph": 3, "offset": 3, "length": 0},
                               "here", doc=doc)
    check("anchored", point.get("success"), True)
    point_listed = bridge.list_comments(doc=doc)
    check("listed", point_listed.get("count"), 1)
    check("with an empty anchor", point_listed["comments"][0]["anchor_text"], "")
    check("adding a comment with no text is refused",
          bridge.add_comment({"paragraph": 3}, "", doc=doc).get("success"), False)

    print("\n--- deleting a comment leaves the text it was anchored to ---")
    point_id = bridge.list_comments(doc=doc)["comments"][0]["id"]
    check("the comment has an id", bool(point_id), True)
    text_before = bridge.read_paragraphs(start=3, count=1,
                                         doc=doc)["paragraphs"][0]["text"]
    removed = bridge.delete_comment(point_id, doc=doc)
    print(removed)
    check("deleted", removed.get("success"), True)
    check("saying what it removed", removed.get("content"), "here")
    check("no comments left", bridge.list_comments(doc=doc)["count"], 0)
    check("the text is untouched",
          bridge.read_paragraphs(start=3, count=1,
                                 doc=doc)["paragraphs"][0]["text"], text_before)
    check("deleting an unknown comment is refused",
          bridge.delete_comment("__Annotation__nope", doc=doc).get("success"),
          False)

    print("\n--- comments by document, section, paragraph, range, selection ---")
    body = doc.getText()
    plain = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    plain.gotoEndOfParagraph(True)
    plain.setString("Alpha beta alpha.")
    plain.HyperLinkURL = ""
    plain.CharStyleName = "Standard"

    first = bridge.add_comment({"paragraph": 1, "offset": 0, "length": 5},
                               "about Alpha", author="Reviewer", doc=doc)
    second = bridge.add_comment({"paragraph": 3}, "about the whole paragraph",
                                author="Claude", doc=doc)
    check("both anchored", (first.get("success"), second.get("success")),
          (True, True))

    everything = bridge.list_comments(doc=doc)
    print(everything)
    check("the document holds two", everything.get("count"), 2)
    ids = [c["id"] for c in everything["comments"]]
    check("with ids of their own", len(set(ids)) == 2 and all(ids), True)
    check("dated with a real date, not a zeroed one",
          all((c["date"] or "").startswith("20")
              for c in everything["comments"]), True)
    check("scope says the whole document", everything.get("scope"),
          {"document": True})

    outline = bridge.get_outline(doc=doc)
    print("headings:", [(h["paragraph"], h["level"], h["text"])
                        for h in outline["headings"]])
    section = bridge.list_comments({"heading": 2}, doc=doc)
    check("the section under 'Section A' holds one", section.get("count"), 1)
    check("which one", section["comments"][0]["content"], "about the whole paragraph")
    check("and the scope names its paragraphs", section["scope"]["paragraphs"][0], 2)
    check("the chapter above holds both",
          bridge.list_comments({"heading": 0}, doc=doc)["count"], 2)
    not_a_heading = bridge.list_comments({"heading": 1}, doc=doc)
    check("a body paragraph is not a section", not_a_heading.get("success"), False)
    check("and says so", "not a heading" in not_a_heading["error"], True)

    check("one paragraph", bridge.list_comments({"paragraph": 1},
                                                doc=doc)["count"], 1)
    check("a paragraph with none", bridge.list_comments({"paragraph": 0},
                                                        doc=doc)["count"], 0)
    check("a range over the anchor",
          bridge.list_comments({"paragraph": 1, "offset": 0, "length": 5},
                               doc=doc)["count"], 1)
    check("a range past it",
          bridge.list_comments({"paragraph": 1, "offset": 6, "length": 4},
                               doc=doc)["count"], 0)

    selectable = body.createTextCursorByRange(
        bridge._paragraph_at(body, 1).getStart())
    selectable.goRight(5, True)
    doc.getCurrentController().select(selectable)
    selected = bridge.list_comments({"selection": True}, doc=doc)
    print(selected)
    check("the selection holds one", selected.get("count"), 1)
    check("the one over the selected words", selected["comments"][0]["content"],
          "about Alpha")

    print("\n--- editing a comment, not the document ---")
    target = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"][0]
    changed = bridge.update_comment(target["id"], text="reworded",
                                    doc=doc)
    print(changed)
    check("changed", changed.get("success"), True)
    check("reporting what changed", changed.get("changed"), ["text"])
    after = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"][0]
    check("the new text is there", after["content"], "reworded")
    check("the author is untouched", after["author"], "Reviewer")
    check("it is the same comment", after["id"], target["id"])
    check("the document text is untouched",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "Alpha beta alpha.")
    check("its anchor still covers the same words",
          after["anchor_text"], "Alpha")

    resolved = bridge.update_comment(target["id"], resolved=True, author="Claude",
                                     doc=doc)
    check("resolved and reassigned", resolved.get("success"), True)
    settled = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"][0]
    check("resolved", settled["resolved"], True)
    check("reassigned", settled["author"], "Claude")
    check("reopened again",
          bridge.update_comment(target["id"], resolved=False,
                                doc=doc).get("resolved"), False)
    check("nothing to change is refused",
          bridge.update_comment(target["id"], doc=doc).get("success"), False)
    check("an unknown id is refused",
          bridge.update_comment("__Annotation__nope", text="x",
                                doc=doc).get("success"), False)

    print("\n--- deleting one comment leaves the others ---")
    survivor = bridge.list_comments({"paragraph": 3}, doc=doc)["comments"][0]
    bridge.delete_comment(target["id"], doc=doc)
    left = bridge.list_comments(doc=doc)
    check("one left", left.get("count"), 1)
    check("the other one", left["comments"][0]["id"], survivor["id"])
    check("its text intact",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "Alpha beta alpha.")
    check("and read_runs no longer reports a comment there",
          [r["comments"] for r in
           bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]], [[]])

    print("\n--- an id survives saving and reopening ---")
    saved_at = tmp_path("mcp_live_comment_ids.odt")
    doc.storeToURL(file_uri(saved_at), ())
    reopened = desktop.loadComponentFromURL(file_uri(saved_at), "_blank", 0, ())
    reloaded = bridge.list_comments(doc=reopened)
    print(reloaded)
    check("the comment came back", reloaded.get("count"), 1)
    check("with the same id", reloaded["comments"][0]["id"], survivor["id"])
    check("with the same text", reloaded["comments"][0]["content"],
          survivor["content"])
    check("editing it by that id works after reopening",
          bridge.update_comment(survivor["id"], text="after reloading",
                                doc=reopened).get("success"), True)
    reopened.setModified(False)
    reopened.close(True)
    os.unlink(saved_at)

    print("\n--- an address in a commented paragraph does not drift ---")
    body = doc.getText()
    plain = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    plain.gotoEndOfParagraph(True)
    plain.setString("query is the entry point")

    def at(offset, length):
        return bridge._resolve_address(
            doc, {"paragraph": 1, "offset": offset, "length": length}
        ).getString()

    check("before any comment", (at(0, 5), at(6, 2), at(10, 3)),
          ("query", "is", "he "))
    first_note = bridge.add_comment({"paragraph": 1, "offset": 0, "length": 5},
                                    "about query", doc=doc)
    check("the anchor is the term", first_note.get("anchor_text"), "query")
    check("an offset past one comment still lands right",
          (at(0, 5), at(6, 2), at(10, 3)), ("query", "is", "he "))

    second_note = bridge.add_comment({"paragraph": 1, "offset": 6, "length": 2},
                                     "about is", doc=doc)
    check("the second anchor is right too", second_note.get("anchor_text"), "is")
    check("an offset past two comments still lands right",
          (at(0, 5), at(6, 2), at(10, 3), at(14, 5)),
          ("query", "is", "he ", "ntry "))
    check("and the comments are where they say they are",
          [(c["address"]["offset"], c["anchor_text"])
           for c in bridge.list_comments({"paragraph": 1}, doc=doc)["comments"]],
          [(0, "query"), (6, "is")])

    edited = bridge.replace_range({"paragraph": 1, "offset": 9, "length": 3},
                                  "THE", doc=doc)
    check("an edit through such an address hits the right characters",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "query is THE entry point")
    check("the edit was allowed", edited.get("success"), True)

    print("\n--- a rewrite keeps a comment on text it does not change ---")
    before = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"]
    print([(c["id"], c["content"], c["anchor_text"], c["date"]) for c in before])
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]
    print("runs:", [(r["text"], len(r["comments"])) for r in runs])

    # A translation that leaves the commented terms alone: only the rest
    # changes, so the annotations must survive untouched.
    translation = {"query": "query", " ": " ", "is": "is",
                   " THE entry point": " — точка входа"}
    translated = [dict(run, text=translation.get(run["text"], run["text"]),
                       language="ru-RU" if run["text"] == " THE entry point"
                       else None)
                  for run in runs]
    kept = bridge.replace_runs({"paragraph": 1}, translated, doc=doc)
    print(kept)
    check("the text was translated",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "query is — точка входа")
    check("both comments were kept, not written again",
          (kept.get("comments_kept"), kept.get("comments_written")), (2, 0))
    check("only the runs outside the comments were rewritten",
          kept.get("runs_rewritten"), 2)

    after = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"]
    print([(c["id"], c["content"], c["anchor_text"], c["date"]) for c in after])
    check("the same comments, by id", [c["id"] for c in after],
          [c["id"] for c in before])
    check("with their dates", [c["date"] for c in after],
          [c["date"] for c in before])
    check("still on their own terms", [c["anchor_text"] for c in after],
          ["query", "is"])

    print("\n--- a rewrite of commented text has to write it again ---")
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]
    retranslated = [dict(run, text="запрос" if run["text"] == "query"
                         else run["text"]) for run in runs]
    again = bridge.replace_runs({"paragraph": 1}, retranslated, doc=doc)
    print(again)
    check("the comment on the changed term was written again",
          again.get("comments_written"), 1)
    check("and the untouched one was kept", again.get("comments_kept"), 1)
    rewritten = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"]
    print([(c["id"], c["content"], c["anchor_text"]) for c in rewritten])
    check("both are there", len(rewritten), 2)
    check("anchored on the new text",
          sorted(c["anchor_text"] for c in rewritten), ["is", "запрос"])

    print("\n--- the language a note's text is written in ---")
    existing = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"]
    check("they are English to begin with",
          sorted({c["language"] for c in existing}), ["en-US"])

    marked = bridge.set_comment_language("ru-RU", doc=doc)
    print(marked)
    check("set", marked.get("success"), True)
    check("reporting the new language", marked.get("language"), "ru-RU")
    check("and what it was", marked.get("was"), "en-US")
    check("counting the ones it does not touch",
          marked.get("comments_already_there"), 3)
    check("the notes already there keep their language",
          sorted({c["language"] for c in
                  bridge.list_comments({"paragraph": 1}, doc=doc)["comments"]}),
          ["en-US"])
    fresh = bridge.add_comment({"paragraph": 3}, "по-русски", doc=doc)
    check("but a note added now is Russian", fresh.get("language"), "ru-RU")
    check("a bad tag is refused",
          bridge.set_comment_language("русский", doc=doc).get("success"), False)
    check("and it can be set back",
          bridge.set_comment_language("en-US", doc=doc).get("language"), "en-US")
    bridge.delete_comment(fresh["id"], doc=doc)

    print("\n--- changing one comment's language means making it again ---")
    target = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"][0]
    other = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"][1]
    remade = bridge.update_comment(target["id"], language="ru-RU", doc=doc)
    print(remade)
    check("done", remade.get("success"), True)
    check("saying it was made again", remade.get("recreated"), True)
    check("naming the comment it replaced", remade.get("previous_id"),
          target["id"])
    check("in the language asked for", remade.get("language"), "ru-RU")
    check("saying the document's comment language went with it",
          remade.get("comment_language_set", {}).get("language"), "ru-RU")
    check("and what it was before", remade.get("comment_language_set",
                                               {}).get("was"), "en-US")
    after = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"]
    print([(c["id"], c["content"], c["language"], c["anchor_text"])
           for c in after])
    check("still two comments", len(after), 2)
    remade_note = [c for c in after if c["id"] == remade["id"]]
    check("the text came with it", [c["content"] for c in remade_note],
          [target["content"]])
    check("the author came with it", [c["author"] for c in remade_note],
          [target["author"]])
    check("and the anchor", [c["anchor_text"] for c in remade_note],
          [target["anchor_text"]])
    check("the other comment is untouched",
          [(c["id"], c["language"]) for c in after if c["id"] != remade["id"]],
          [(other["id"], other["language"])])
    check("the document text is untouched",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "запрос is — точка входа")
    plain = bridge.add_comment({"paragraph": 3}, "как получится", doc=doc)
    check("a comment added afterwards follows that setting",
          plain.get("language"), "ru-RU")
    asked = bridge.add_comment({"paragraph": 3, "offset": 0, "length": 3},
                               "in English", language="en-US", doc=doc)
    check("and asking for another language works, document-wide",
          (asked.get("language"),
           asked.get("comment_language_set", {}).get("language")),
          ("en-US", "en-US"))

    print("\n--- pictures: a selection that holds one ---")
    body = doc.getText()
    plain = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    plain.gotoEndOfParagraph(True)
    plain.setString("query is the entry point")
    for comment in bridge.list_comments({"paragraph": 1}, doc=doc)["comments"]:
        bridge.delete_comment(comment["id"], doc=doc)

    from com.sun.star.text.TextContentAnchorType import (AS_CHARACTER,
                                                         AT_CHARACTER)
    provider = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.graphic.GraphicProvider", ctx)
    picture_file = write_test_png(tmp_path("mcp_live_source.png"))
    source = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    source.Name, source.Value = "URL", file_uri(picture_file)

    def put_picture(offset, name, inline=True, title="", description=""):
        picture = doc.createInstance("com.sun.star.text.TextGraphicObject")
        picture.Graphic = provider.queryGraphic((source,))
        picture.AnchorType = AS_CHARACTER if inline else AT_CHARACTER
        picture.Name = name
        picture.Width, picture.Height = 2434, 2452
        picture.Title, picture.Description = title, description
        span = bridge._resolve_address(doc, {"paragraph": 1, "offset": offset,
                                             "length": 0})
        span.getText().insertTextContent(span, picture, False)

    put_picture(6, "Schema", title="GraphQL schema",
                description="a query diagram")
    listed = bridge.list_images(number=True, doc=doc)
    print(listed)
    check("the document holds one picture", listed.get("count"), 1)
    check("and the fast listing places it by anchor alone",
          bridge.list_images(doc=doc)["images"][0]["address"]["anchor"]["type"],
          "text")
    picture = listed["images"][0]
    check("named", picture["name"], "Schema")
    check("inline in the text", picture["inline"], True)
    check("with the address of its anchor",
          {key: value for key, value in picture["address"].items()
           if key != "anchor"},
          {"paragraph": 1, "offset": 6, "length": 0})
    check("the text it is anchored to", picture["paragraph_text"],
          "query is the entry point")
    check("its size in millimetres", (picture["width_mm"], picture["height_mm"]),
          (24.3, 24.5))
    check("its size in pixels", picture["pixels"], {"width": 8, "height": 8})
    check("its title", picture["title"], "GraphQL schema")
    check("its alternative text", picture["description"], "a query diagram")

    selectable = body.createTextCursorByRange(
        bridge._paragraph_at(body, 1).getStart())
    selectable.goRight(12, True)
    doc.getCurrentController().select(selectable)
    check("the selection is seen to hold it",
          bridge.list_images({"selection": True}, doc=doc)["count"], 1)
    check("a paragraph without one holds none",
          bridge.list_images({"paragraph": 0}, doc=doc)["count"], 0)
    check("a range before it holds none",
          bridge.list_images({"paragraph": 1, "offset": 0, "length": 5},
                             doc=doc)["count"], 0)

    print("\n--- and read_runs says which run it sits in ---")
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]
    print([(r["text"], [i["name"] for i in r["images"]]) for r in runs])
    check("the run that starts at the anchor carries it",
          [bool(r["images"]) for r in runs], [False, True])
    check("and the picture costs no characters",
          sum(r["length"] for r in runs), len("query is the entry point"))

    print("\n--- the file, and the picture itself ---")
    written = bridge.export_image("Schema", path=tmp_path("mcp_live_export.png"),
                                  doc=doc)
    print({k: v for k, v in written.items() if k != "_image_content"})
    check("written", written.get("success"), True)
    check("as a PNG", open(tmp_path("mcp_live_export.png"), "rb").read(8),
          b"\x89PNG\r\n\x1a\x0a")
    check("of the size it reports", written.get("bytes"),
          os.path.getsize(tmp_path("mcp_live_export.png")))
    check("carrying the address of the picture", written.get("address"),
          {"paragraph": 1, "offset": 6, "length": 0})
    inline_result = bridge.export_image("Schema", path=tmp_path("mcp_live_inline.png"),
                                        inline=True, doc=doc)
    check("handed back for looking at", inline_result.get("inline"), True)
    check("as base64 of a PNG",
          inline_result["_image_content"]["data"].startswith("iVBORw0KGgo"),
          True)
    check("an unknown picture is refused",
          bridge.export_image("Nope", doc=doc).get("success"), False)
    for leftover in (tmp_path("mcp_live_export.png"), tmp_path("mcp_live_inline.png")):
        os.unlink(leftover)

    print("\n--- a rewrite that would destroy it is refused ---")
    refused = bridge.replace_range({"paragraph": 1}, "translation", doc=doc)
    print(refused)
    check("refused", refused.get("success"), False)
    check("naming the picture", "inline picture" in refused["error"], True)
    check("saying it would be destroyed outright",
          "destroyed outright" in refused["error"], True)
    check("and the picture is still there",
          bridge.list_images(doc=doc)["count"], 1)

    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]
    changing = [dict(run, text="— точка входа") if run["images"] else dict(run)
                for run in runs]
    refused_runs = bridge.replace_runs({"paragraph": 1}, changing, doc=doc)
    print(refused_runs)
    check("replace_runs refuses it too", refused_runs.get("success"), False)
    check("naming the picture by name", "Schema" in refused_runs["error"], True)
    check("the picture survived that", bridge.list_images(doc=doc)["count"], 1)

    print("\n--- rewriting the text before it keeps it ---")
    keeping = [dict(run) if run["images"] else dict(run, text="запрос ")
               for run in runs]
    kept_picture = bridge.replace_runs({"paragraph": 1}, keeping, doc=doc)
    print(kept_picture)
    check("allowed", kept_picture.get("success"), True)
    check("reporting the picture it kept", kept_picture.get("images_kept"), 1)
    check("the text changed",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "запрос is the entry point")
    after = bridge.list_images(number=True, doc=doc)
    check("the picture is there", after.get("count"), 1)
    check("with its anchor where the text put it",
          {key: value for key, value in after["images"][0]["address"].items()
           if key != "anchor"},
          {"paragraph": 1, "offset": 7, "length": 0})
    check("and its own name", after["images"][0]["name"], "Schema")

    print("\n--- flatten=true destroys it, and says so ---")
    flattened = bridge.replace_range({"paragraph": 1}, "a whole translation",
                                     flatten=True, doc=doc)
    print(flattened)
    check("went ahead", flattened.get("success"), True)
    check("reporting the picture it destroyed", flattened.get("images_dropped"),
          1)
    check("and it is gone", bridge.list_images(doc=doc)["count"], 0)

    print("\n--- a picture anchored to a character is no reason to refuse ---")
    plain = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    plain.gotoEndOfParagraph(True)
    plain.setString("query is the entry point")
    put_picture(6, "Anchored", inline=False)
    anchored = bridge.list_images(doc=doc)["images"][0]
    check("reported as not inline", anchored["inline"], False)
    check("with its anchor kind", anchored["anchor"], "AT_CHARACTER")
    allowed = bridge.replace_range({"paragraph": 1}, "translation", flatten=True,
                                   doc=doc)
    check("the rewrite went ahead", allowed.get("success"), True)
    check("nothing was reported destroyed", allowed.get("images_dropped"), 0)
    check("and it survived", bridge.list_images(doc=doc)["count"], 1)

    print("\n--- a replacement over a point is refused, not written ---")
    before_text = bridge.read_paragraphs(start=1, count=1,
                                         doc=doc)["paragraphs"][0]["text"]
    caret = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    caret.goRight(3, False)
    doc.getCurrentController().select(caret)
    point = bridge.replace_range({"selection": True}, "PROBE", doc=doc)
    print(point)
    check("refused", point.get("success"), False)
    check("saying there is nothing to replace",
          "nothing to replace" in point["error"], True)
    explicit = bridge.replace_range({"paragraph": 1, "offset": 3, "length": 0},
                                    "PROBE", doc=doc)
    check("an explicit zero length too", explicit.get("success"), False)
    check("and nothing was written",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"], before_text)

    print("\n--- tables: knowing the caret is in one, and reading it ---")
    tables = doc.getTextTables()
    table_name = tables.getElementNames()[0]
    table = tables.getByName(table_name)
    table.getCellByName("A1").setString("Operation")
    table.getCellByName("B1").setString("Response")
    table.getCellByName("A2").setString("{\n  hero {\n    name\n  }\n}")
    table.getCellByName("B2").setString("R2-D2")

    listed = bridge.list_tables(number=True, doc=doc)
    print("   ", listed)
    check("the table is listed", listed.get("count"), 1)
    fast = bridge.list_tables(doc=doc)["tables"][0]
    check("the fast listing skips the walk that places it",
          (fast["after_paragraph"], fast["name"]), (None, "Table1"))
    described = listed["tables"][0]
    check("with its size", (described["rows"], described["columns"]), (2, 2))
    check("its cells", described["cells"], 4)
    check("the share of each column", described["column_widths_percent"],
          [50.0, 50.0])
    check("and where it sits in the text",
          isinstance(described["after_paragraph"], int), True)
    check("no invented millimetres", "width_mm" in described, False)

    # A paragraph has no number in UNO, so counting one is a walk of the
    # body — three seconds with the caret deep in a real guide. The caret
    # comes back as an anchor instead, and the number only when asked.
    where = bridge.get_cursor_info(doc=doc)
    check("the caret is reported without a number",
          (where["cursor"]["paragraph_index"],
           where["cursor"]["document_offset"]), (None, None))
    check("the anchor says which kind it is, inside the address and nowhere else",
          (where["address"]["anchor"]["type"], "anchor" in where),
          ("paragraph", False))
    check("and with an anchor that names the paragraph it reported",
          bridge._resolve_address(doc, where["address"]).getString(),
          where["paragraph"]["text"])
    numbered = bridge.get_cursor_info(number=True, character_offset=True,
                                      doc=doc)
    check("the number and the character offset come when they are asked for",
          (isinstance(numbered["cursor"]["paragraph_index"], int),
           isinstance(numbered["cursor"]["document_offset"], int),
           "note" in numbered),
          (True, True, False))

    # And what the caret's anchor is for: reading there without the number
    # ever being worked out, with the runs addressed by the same anchor.
    through = bridge.read_runs(where["address"], doc=doc)
    check("the runs at the caret are read through its anchor",
          (through.get("success"), through.get("paragraph"),
           through["address"]["anchor"]["anchorId"]),
          (True, None, where["address"]["anchor"]["anchorId"]))
    check("and every run of that read resolves through it too",
          [bridge._resolve_address(doc, run["address"]).getString()
           for run in through["runs"]],
          [run["text"] for run in through["runs"]])

    print("\n   with the caret put inside a cell:")
    view = doc.getCurrentController().getViewCursor()
    was_here = view.getStart()
    view.gotoRange(table.getCellByName("A2").getStart(), False)
    info = bridge.get_cursor_info(doc=doc)
    print("   ", info.get("in_table"))
    check("the caret is known to be in a table",
          info.get("in_table", {}).get("table"), table_name)
    check("and in which cell", info["in_table"]["cell"], "A2")
    check("with its row and column",
          (info["in_table"]["row"], info["in_table"]["column"]), (2, 1))
    check("the cell's text comes with it",
          info["in_table"]["cell_text"].splitlines()[0], "{")
    check("and there is honestly no body paragraph",
          info["cursor"]["paragraph_index"], None)
    check("the listing says where the caret is",
          bridge.list_tables(doc=doc)["caret_is_in"],
          {"table": table_name, "cell": "A2"})

    print("\n   reading the table the caret is in:")
    read = bridge.read_table(doc=doc)
    print("   ", [[cell["text"][:12] if cell else None for cell in row]
                  for row in read["rows"]])
    check("read without being named", read["table"]["name"], table_name)
    check("the grid is the right shape",
          [[cell["cell"] for cell in row] for row in read["rows"]],
          [["A1", "B1"], ["A2", "B2"]])
    check("the header cells", (read["rows"][0][0]["text"],
                               read["rows"][0][1]["text"]),
          ("Operation", "Response"))
    check("a cell of several paragraphs keeps its line breaks",
          len(read["rows"][1][0]["text"].splitlines()), 5)
    check("and it says which cell the caret is in", read["caret_in_cell"], "A2")

    one = bridge.read_table(table_name, cell="B2", doc=doc)
    check("one cell can be read on its own", one.get("text"), "R2-D2")
    check("with its position", (one["row"], one["column"]), (2, 2))
    check("a cell that is not there is refused",
          bridge.read_table(table_name, cell="Z9", doc=doc).get("success"),
          False)
    check("a table that is not there is refused",
          bridge.read_table("Table99", doc=doc).get("success"), False)

    view.gotoRange(was_here, False)
    check("the caret is back out of the table",
          bridge.get_cursor_info(doc=doc).get("in_table"), None)

    print("\n--- a comment on what is selected ---")
    body = doc.getText()
    mark = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    mark.gotoEndOfParagraph(True)
    doc.getCurrentController().select(mark)
    commented = bridge.add_comment({"selection": True}, "on the selection", doc=doc)
    print("   ", commented)
    check("a comment goes on the selection", commented.get("success"), True)
    check("anchored to what was selected", commented.get("anchor_text"),
          mark.getString())
    bridge.delete_comment(commented["id"], doc=doc)

    print("\n--- a block of paragraphs is one address ---")
    body = doc.getText()
    tail = body.createTextCursorByRange(body.getEnd())
    for line in ("BLOCK-START", "{", "  hero {", "    name", "  }", "}",
                 "BLOCK-END"):
        body.insertControlCharacter(tail, PARAGRAPH_BREAK, False)
        body.insertString(tail, line, False)
    total = bridge.read_paragraphs(start=0, count=200, doc=doc)["count"]
    first = total - 7
    last = total - 1

    span = bridge._resolve_address(doc, {"paragraph": first, "through": last})
    print("   the block reads:", repr(span.getString()[:40]), "…",
          len(span.getString()), "chars")
    check("a block resolves to all of it",
          span.getString().splitlines()[0], "BLOCK-START")
    check("through the last of them",
          span.getString().splitlines()[-1], "BLOCK-END")
    check("a block that runs backwards is refused",
          _refused(bridge, doc, {"paragraph": last, "through": first}), True)
    check("and one that takes an offset too",
          _refused(bridge, doc, {"paragraph": first, "through": last,
                                 "offset": 2}), True)

    selected = bridge.select({"paragraph": first, "through": last},
                             number=True, doc=doc)
    print("   ", {k: v for k, v in selected.items() if k != "selected"})
    check("selecting it works", selected.get("success"), True)
    check("over all its paragraphs", selected.get("paragraphs"),
          list(range(first, last + 1)))
    check("and the document's selection really is that",
          bridge._resolve_address(doc, {"selection": True}).getString()
          == span.getString(), True)

    made = bridge.create_table({"paragraph": first, "through": last}, rows=2,
                               columns=2,
                               cells=[["Operation", "Response"],
                                      ["{ hero }", '{ "R2-D2" }']],
                               name="FromBlock", replace=True, doc=doc)
    print("   ", made)
    check("a table replaces the whole block", made.get("success"), True)
    check("saying which paragraphs went",
          made.get("paragraphs_replaced"), list(range(first, last + 1)))
    check("and they did",
          bridge.read_paragraphs(start=0, count=200, doc=doc)["count"],
          total - 7)
    check("while the table stands there",
          bridge.read_table("FromBlock", cell="A1", doc=doc)["text"], "Operation")
    bridge.delete_table("FromBlock", doc=doc)

    print("\n--- making a table, and taking one away ---")
    body = doc.getText()
    before_tables = len(doc.getTextTables().getElementNames())
    made = bridge.create_table({"paragraph": 1}, rows=2, columns=2,
                               cells=[["Operation", "Response"],
                                      ["{ hero { name } }", '{ "R2-D2" }']],
                               name="LiveExample", header_rows=1,
                               repeat_heading=True, doc=doc)
    print("   ", made)
    check("made", made.get("success"), True)
    check("with the name asked for", made.get("table"), "LiveExample")
    check("and every cell filled", made.get("cells_filled"), 4)
    read = bridge.read_table("LiveExample", doc=doc)
    check("holding what it was given",
          [[cell["text"] for cell in row] for row in read["rows"]],
          [["Operation", "Response"], ["{ hero { name } }", '{ "R2-D2" }']])
    check("marked as having a heading", read["table"]["header_rows"], 1)
    check("the document has one more table",
          len(doc.getTextTables().getElementNames()), before_tables + 1)
    check("a name already taken is refused",
          bridge.create_table({"paragraph": 1}, name="LiveExample",
                              doc=doc).get("success"), False)
    check("a silly size is refused",
          bridge.create_table({"paragraph": 1}, rows=0,
                              doc=doc).get("success"), False)

    print("\n   the whole point: text replaced by a table")
    # A paragraph of this section's own, so the sections after it find the
    # document as they expect it — they share one, which is easy to forget.
    tail = body.createTextCursorByRange(body.getEnd())
    body.insertControlCharacter(tail, PARAGRAPH_BREAK, False)
    body.insertString(tail, "Operation: { hero }", False)
    paragraphs_before = bridge.read_paragraphs(start=0, count=60,
                                               doc=doc)["count"]
    mine = paragraphs_before - 1
    replaced = bridge.create_table({"paragraph": mine}, rows=1, columns=2,
                                   cells=[["Operation", "{ hero }"]],
                                   name="InsteadOfText", replace=True, doc=doc)
    print("   ", replaced)
    check("the table stands in its place", replaced.get("success"), True)
    check("saying which paragraphs it replaced",
          replaced.get("paragraphs_replaced"), [mine])
    check("and they are gone",
          bridge.read_paragraphs(start=0, count=60, doc=doc)["count"],
          paragraphs_before - 1)
    check("while the table holds their text",
          bridge.read_table("InsteadOfText", cell="B1", doc=doc)["text"],
          "{ hero }")

    print("\n   and taking them away again")
    removed = bridge.delete_table("InsteadOfText", doc=doc)
    print("   ", {k: v for k, v in removed.items() if k != "held"})
    check("removed", removed.get("success"), True)
    check("saying what it held",
          sorted(entry["text"] for entry in removed["held"]),
          sorted(["Operation", "{ hero }"]))
    check("a table that is not there is refused",
          bridge.delete_table("No such table", doc=doc).get("success"), False)
    bridge.delete_table("LiveExample", doc=doc)
    check("the document is back to the tables it had",
          len(doc.getTextTables().getElementNames()), before_tables)

    print("\n--- a cell has an address of its own ---")
    table.getCellByName("A1").setString("Operation")
    table.getCellByName("B1").setString("Response")
    table.getCellByName("A2").setString("{\n  hero {\n    name\n  }\n}")
    table.getCellByName("B2").setString("R2-D2")

    whole = bridge._resolve_address(doc, {"table": table_name, "cell": "A1"})
    check("a cell resolves to its text", whole.getString(), "Operation")
    part = bridge._resolve_address(doc, {"table": table_name, "cell": "A1",
                                         "offset": 0, "length": 5})
    check("and part of a cell to part of it", part.getString(), "Opera")
    across = bridge._resolve_address(doc, {"table": table_name, "cell": "A2",
                                           "offset": 2, "length": 8})
    check("an offset counts across the paragraphs of a cell",
          across.getString(), "  hero {")

    print("\n   what find_text now says about a hit inside a cell:")
    hits = bridge.find_text("R2-D2", doc=doc)
    in_cell = [hit for hit in hits["hits"]
               if (hit["address"] or {}).get("cell")]
    print("   ", in_cell[:1])
    check("a hit in a cell carries a cell address", bool(in_cell), True)
    check("naming the table and the cell",
          (in_cell[0]["address"]["table"], in_cell[0]["address"]["cell"]),
          (table_name, "B2"))
    check("and that address resolves back to the hit",
          bridge._resolve_address(doc, in_cell[0]["address"]).getString(),
          "R2-D2")

    print("\n   the text tools, in a cell:")
    runs = bridge.read_runs({"table": table_name, "cell": "A1"}, doc=doc)
    check("read_runs reads a cell", [run["text"] for run in runs["runs"]],
          ["Operation"])
    rewritten = bridge.replace_range({"table": table_name, "cell": "B2"},
                                     "R2-D2 and C-3PO", doc=doc)
    check("replace_range writes into a cell", rewritten.get("success"), True)
    check("and the cell holds it",
          bridge.read_table(table_name, cell="B2", doc=doc)["text"],
          "R2-D2 and C-3PO")
    check("apply_paragraph_style works there",
          bridge.apply_paragraph_style({"table": table_name, "cell": "A2"},
                                       "Preformatted Text",
                                       doc=doc).get("success"), True)
    check("set_language works there",
          bridge.set_language({"table": table_name, "cell": "A1"}, "en-US",
                              doc=doc).get("success"), True)
    check("format_range works there",
          bridge.format_range({"table": table_name, "cell": "B1"}, bold=True,
                              doc=doc).get("success"), True)
    commented = bridge.add_comment({"table": table_name, "cell": "A1"},
                                   "A term", doc=doc)
    check("a comment can be anchored in a cell", commented.get("success"), True)
    check("on the cell's text", commented.get("anchor_text"), "Operation")
    listed_comments = [comment for comment
                       in bridge.list_comments(doc=doc)["comments"]
                       if (comment["address"] or {}).get("cell")]
    check("and it is listed with a cell address",
          listed_comments[0]["address"]["cell"] if listed_comments else None,
          "A1")
    for comment in listed_comments:
        bridge.delete_comment(comment["id"], doc=doc)
    check("describing the style in a cell works",
          bridge.describe_style(address={"table": table_name, "cell": "A2"},
                                doc=doc)["style"]["name"],
          "Preformatted Text")

    print("\n   formatting the text inside the cells:")
    table.getCellByName("A1").setString("Operation")
    table.getCellByName("B1").setString("Response")
    table.getCellByName("A2").setString("{\n  hero {\n    name\n  }\n}")
    for cell in ("A1", "B1"):
        check(f"{cell} takes the heading style",
              bridge.apply_paragraph_style({"table": table_name, "cell": cell},
                                           "Table Heading",
                                           doc=doc).get("success"), True)
    check("the code row takes a monospace style",
          bridge.format_table(table_name, cells="row:2",
                              paragraph_style="Preformatted Text",
                              doc=doc).get("success"), True)

    painted = 0
    for piece, colour in (("hero", "#0B7285"), ("name", "#0B7285")):
        whole = bridge.read_table(table_name, cell="A2", doc=doc)["text"]
        at = whole.find(piece)
        if at < 0:
            continue
        result = bridge.format_range({"table": table_name, "cell": "A2",
                                      "offset": at, "length": len(piece)},
                                     color=colour, doc=doc)
        painted += 1 if result.get("success") else 0
    check("pieces of a cell can be coloured", painted, 2)

    cell_runs = bridge.read_runs({"table": table_name, "cell": "A2"},
                                 doc=doc)["runs"]
    print("   runs of A2:", [(run["text"][:12], run["color"])
                             for run in cell_runs])
    check("the runs of a cell are addressed to that cell",
          (cell_runs[0]["address"].get("table"),
           cell_runs[0]["address"].get("cell")), (table_name, "A2"))
    check("and every one resolves back to its own text",
          all(bridge._resolve_address(doc, run["address"]).getString()
              == run["text"] for run in cell_runs), True)
    check("the colours really are on the runs",
          sorted({run["color"] for run in cell_runs if run["color"]}),
          ["#0B7285"])

    rewritten = bridge.replace_runs(
        {"table": table_name, "cell": "A2"},
        [dict(run, text=run["text"].replace("name", "имя"))
         for run in cell_runs], doc=doc)
    print("   replace_runs in a cell:", rewritten)
    check("a cell survives a rewrite through its runs",
          rewritten.get("success"), True)
    after = bridge.read_runs({"table": table_name, "cell": "A2"},
                             doc=doc)["runs"]
    check("the text changed",
          "имя" in bridge.read_table(table_name, cell="A2", doc=doc)["text"],
          True)
    check("and the colours came through",
          sorted({run["color"] for run in after if run["color"]}), ["#0B7285"])

    print("\n   refusals:")
    for label, address in (
            ("no such table", {"table": "Nope", "cell": "A1"}),
            ("no such cell", {"table": table_name, "cell": "Z9"}),
            ("no cell named", {"table": table_name}),
            ("offset past the end", {"table": table_name, "cell": "A1",
                                     "offset": 99})):
        try:
            bridge._resolve_address(doc, address)
            check(f"{label} is refused", "not refused", "refused")
        except Exception as e:
            print(f"      {label}: {str(e)[:70]}")
            check(f"{label} is refused", True, True)

    print("\n--- giving a table a look ---")
    formatted = bridge.format_table(
        table_name, border=True, border_color="#B0B0B0", border_width=0.5,
        padding_mm=1.5, background_color="#F7F7F7", header_rows=1,
        repeat_heading=True, header_background_color="#E4E4E4",
        header_bold=True, column_widths_percent=[45, 55], doc=doc)
    print("   ", formatted)
    check("formatted", formatted.get("success"), True)
    check("touching every cell", formatted.get("cells_touched"), 4)

    shape = table.TableBorder2
    check("the outline is drawn", shape.TopLine.LineWidth, 49)
    check("in the colour asked for", shape.TopLine.Color, 0xB0B0B0)
    check("the lines between the cells too", shape.HorizontalLine.LineWidth, 49)
    check("with the padding", shape.Distance, 150)
    check("the heading is marked",
          (table.HeaderRowCount, table.RepeatHeadline), (1, True))
    check("the heading has its own background",
          table.getCellByName("A1").BackColor, 0xE4E4E4)
    check("and the body its own",
          table.getCellByName("A2").BackColor, 0xF7F7F7)
    check("backgrounds are not transparent, or nothing shows",
          table.getCellByName("A2").BackTransparent, False)
    check("the columns moved",
          [separator.Position for separator
           in table.TableColumnSeparators][0] in (4499, 4500, 4501), True)

    code = bridge.format_table(table_name, cells="row:2",
                               paragraph_style="Preformatted Text",
                               font_size=9, doc=doc)
    print("   ", code)
    check("the code cells took a style", code.get("cells_touched"), 2)
    styles = []
    paragraphs = table.getCellByName("A2").createEnumeration()
    while paragraphs.hasMoreElements():
        styles.append(paragraphs.nextElement().ParaStyleName)
    check("which really is on the paragraphs", sorted(set(styles)),
          ["Preformatted Text"])

    check("a cell that is not there is refused",
          bridge.format_table(table_name, cells=["Z9"], border=True,
                              doc=doc).get("success"), False)
    check("a style the document lacks is refused",
          bridge.format_table(table_name, paragraph_style="Nope",
                              doc=doc).get("success"), False)
    check("shares that do not add up are refused",
          bridge.format_table(table_name, column_widths_percent=[10, 10],
                              doc=doc).get("success"), False)
    check("asking for nothing is refused",
          bridge.format_table(table_name, doc=doc).get("success"), False)
    check("one undo step per formatting call",
          bridge.format_table(table_name, background_color="#FAFAFA",
                              doc=doc).get("success"), True)

    print("\n--- a hit that brings its block along ---")
    # Paragraphs of this section's own: the ones the document started with
    # have been rewritten by the checks above, and borrowing them is how a
    # section comes to depend on what ran before it.
    marker = body.createTextCursorByRange(body.getEnd())
    for line in ("SEARCH-BEACON", "first after", "second after"):
        body.insertControlCharacter(marker, PARAGRAPH_BREAK, False)
        body.insertString(marker, line, False)

    found = bridge.find_text("SEARCH-BEACON", paragraphs_after=2,
                             paragraphs_before=1, doc=doc)
    print("   ", found["hits"][0] if found["hits"] else found)
    check("the hit is there", found.get("total_hits"), 1)
    first = found["hits"][0]
    at = first["address"]["paragraph"]
    check("with the paragraphs after it",
          [entry["text"] for entry in first["after"]],
          ["first after", "second after"])
    check("and the one before",
          [entry["paragraph"] for entry in first["before"]], [at - 1])
    check("each with its style",
          all(entry["style"] for entry in first["after"]), True)
    check("asking for too much neighbourhood is refused",
          bridge.find_text("SEARCH-BEACON", paragraphs_after=500,
                           doc=doc).get("success"), False)
    check("and without asking, none come",
          "after" in bridge.find_text("SEARCH-BEACON", doc=doc)["hits"][0],
          False)

    in_cell = bridge.find_text("Operation", paragraphs_after=2, doc=doc)
    cell_hits = [hit for hit in in_cell["hits"]
                 if (hit["address"] or {}).get("cell")]
    if cell_hits:
        check("a hit inside a cell has no body neighbours",
              cell_hits[0]["after"], None)
        check("but still carries its cell address",
              bool(cell_hits[0]["address"]["table"]), True)

    for _ in range(3):                      # take the marker paragraphs away
        last = bridge._paragraph_at(body, bridge.read_paragraphs(
            start=0, count=1, doc=doc)["total_paragraphs"] - 1)
        body.removeTextContent(last)

    print("\n--- colouring many pieces in one call ---")
    table.getCellByName("A2").setString("{\n  hero {\n    name\n  }\n}")
    whole = bridge.read_table(table_name, cell="A2", doc=doc)["text"]
    spans = []
    for piece, colour in (("hero", "#0B7285"), ("name", "#0B7285")):
        at = whole.find(piece)
        if at >= 0:
            spans.append({"address": {"table": table_name, "cell": "A2",
                                      "offset": at, "length": len(piece)},
                          "color": colour})
    for position, character in enumerate(whole):
        if character in "{}":
            spans.append({"address": {"table": table_name, "cell": "A2",
                                      "offset": position, "length": 1},
                          "color": "#868E96"})
    painted = bridge.format_ranges(spans, doc=doc)
    print("   ", painted)
    check("all of them in one call", painted.get("ranges"), len(spans))
    coloured = [run for run in bridge.read_runs({"table": table_name,
                                                 "cell": "A2"},
                                                doc=doc)["runs"]
                if run["color"]]
    check("and the colours are on the text",
          sorted({run["color"] for run in coloured}), ["#0B7285", "#868E96"])

    check("a bad address stops the lot before anything is written",
          bridge.format_ranges([
              {"address": {"paragraph": 1, "offset": 0, "length": 1},
               "color": "#111111"},
              {"address": {"paragraph": 99999}, "color": "#222222"}],
              doc=doc).get("success"), False)
    check("and an entry that asks for nothing is refused",
          bridge.format_ranges([{"address": {"paragraph": 1, "offset": 0,
                                             "length": 1}}],
                               doc=doc).get("success"), False)

    print("\n--- reading a table's look, instead of unzipping the file ---")
    bridge.format_table(table_name, border=True, border_color="#B0B0B0",
                        border_width=0.35, padding_mm=2.0,
                        background_color="#F7F7F7", header_rows=1,
                        header_background_color="#EFEFEF", doc=doc)
    described = bridge.describe_table(table_name, runs=True, doc=doc)
    print("   table:", {k: v for k, v in described["table"].items()
                        if k in ("border", "padding_mm", "header_rows",
                                 "column_widths_percent")})
    check("described", described.get("success"), True)
    check("the grid outside", described["table"]["border"]["outer"],
          {"width_mm": 0.35, "color": "#B0B0B0"})
    check("and between the cells", described["table"]["border"]["inner"],
          {"width_mm": 0.35, "color": "#B0B0B0"})
    check("the padding", described["table"]["padding_mm"], 2.0)
    cells = {cell["cell"]: cell for cell in described["cells"]}
    print("   cells:", {name: (cell["background_color"],
                               cell["paragraph_styles"])
                        for name, cell in cells.items()})
    check("the heading's background", cells["A1"]["background_color"],
          "#EFEFEF")
    check("the body's background", cells["A2"]["background_color"], "#F7F7F7")
    check("the styles the cells use",
          bool(cells["A2"]["paragraph_styles"]), True)
    check("and the coloured pieces of their text",
          any(run["color"] for run in cells["A2"]["runs"]), True)
    check("describing a table that is not there is refused",
          bridge.describe_table("No such table", doc=doc).get("success"), False)

    print("\n   and the look reads back into format_table:")
    look = described["table"]
    second = bridge.create_table({"paragraph": 1}, rows=2, columns=2,
                                 name="Copy", doc=doc)
    check("a second table was made", second.get("success"), True)
    copied = bridge.format_table(
        "Copy", border=True,
        border_color=look["border"]["outer"]["color"],
        border_width=look["border"]["outer"]["width_mm"],
        padding_mm=look["padding_mm"],
        background_color=cells["A2"]["background_color"],
        header_rows=look["header_rows"],
        header_background_color=cells["A1"]["background_color"], doc=doc)
    check("dressed from what was read", copied.get("success"), True)
    twin = bridge.describe_table("Copy", doc=doc)
    check("and the copy looks like the original",
          (twin["table"]["border"]["outer"], twin["table"]["padding_mm"]),
          (look["border"]["outer"], look["padding_mm"]))
    bridge.delete_table("Copy", doc=doc)

    print("\n--- a selection running from text through a table ---")
    body = doc.getText()
    across = body.createTextCursorByRange(bridge._paragraph_at(body, 3).getStart())
    across.gotoEnd(True)          # from before the table to the end
    doc.getCurrentController().select(across)
    print("   the selection's own string:",
          repr(across.getString()[:70]), "…", len(across.getString()), "chars")

    info = bridge.get_cursor_info(doc=doc)
    selected = info["selection"]
    print("   selection:", {k: v for k, v in selected.items()
                            if k not in ("text",)})
    check("it says a table is in there", selected.get("contains_table"), True)
    check("naming it", [table["name"] for table in selected["tables"]],
          [table_name])
    check("with its size",
          (selected["tables"][0]["rows"], selected["tables"][0]["columns"]),
          (2, 2))
    check("and how many body paragraphs it covers, with an anchor over it all",
          (selected["paragraphs_selected"],
           selected["address"]["anchor"]["type"] == "text",
           "paragraphs" in selected),
          (1, True, False))          # one paragraph, then the table
    check("the numbers still come when they are asked for",
          bridge.get_cursor_info(number=True,
                                 doc=doc)["selection"]["paragraphs"][0], 3)

    # A selection over two plain paragraphs: read through its anchor, both
    # come back, each held by an anchor of its own and no number counted.
    two = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    two.gotoRange(bridge._paragraph_at(body, 2).getEnd(), True)
    doc.getCurrentController().select(two)
    spread = bridge.get_cursor_info(doc=doc)["selection"]
    check("a selection of two paragraphs says so", 
          (spread["paragraphs_selected"], spread["contains_table"]), (2, False))
    read = bridge.read_runs(spread["address"], doc=doc)
    check("and its anchor reads them both, each with an anchor of its own",
          (read.get("paragraphs_read"),
           len({run["address"]["anchor"]["anchorId"] for run in read["runs"]}),
           read.get("paragraph")),
          (2, 2, None))
    check("every run of it resolves through the anchor it came with",
          [bridge._resolve_address(doc, run["address"]).getString()
           for run in read["runs"]],
          [run["text"] for run in read["runs"]])
    doc.getCurrentController().select(across)
    check("the listing agrees", bridge.list_tables(doc=doc).get("in_selection"),
          [table_name])

    runs = bridge.read_runs({"selection": True}, doc=doc)
    print("   read_runs:", runs.get("count"), "runs |",
          runs.get("spans_tables"), "|", runs.get("note", "")[:60])
    check("reading runs says what it cannot reach",
          runs.get("spans_tables"), [{"name": table_name, "rows": 2,
                                      "columns": 2}])

    refused = bridge.replace_selection("translation", doc=doc)
    print("   replace_selection:", refused)
    check("replacing it is refused", refused.get("success"), False)
    check("naming the table and its size",
          f"{table_name} (2x2)" in refused["error"], True)
    check("the table is untouched",
          len(doc.getTextTables().getElementNames()), 1)

    print("\n   and with flatten, the damage is done and counted:")
    before_paragraphs = bridge.read_paragraphs(start=3, count=1,
                                               doc=doc)["paragraphs"][0]["text"]
    flattened = bridge.replace_selection("all replaced", flatten=True, doc=doc)
    print("   ", flattened)
    check("went ahead", flattened.get("success"), True)
    check("counting the table it destroyed", flattened.get("tables_dropped"), 1)
    check("and the table really is gone",
          len(doc.getTextTables().getElementNames()), 0)

    # put a table back for the checks that follow
    replacement = doc.createInstance("com.sun.star.text.TextTable")
    replacement.initialize(2, 2)
    tail = body.createTextCursorByRange(body.getEnd())
    body.insertTextContent(tail, replacement, False)
    table_name = replacement.Name
    table = replacement
    doc.getCurrentController().select(body.createTextCursorByRange(
        bridge._paragraph_at(body, 1).getStart()))

    print("\n--- describing a style: its own definition, and what is in force ---")
    described = bridge.describe_style("Text body", doc=doc)
    print("   identity:", described["style"])
    check("described", described.get("success"), True)
    check("its parent", described["style"]["parent"], "Standard")
    check("the chain it inherits from", described["style"]["inherits_from"],
          ["Standard"])
    check("the style that follows it", described["style"]["next_style"],
          "Text body")
    check("built in, not made by the user", described["style"]["user_defined"],
          False)

    print("   sets itself:", {name: entry["value"]
                              for name, entry in described["set_here"].items()})
    check("a handful of the lot, not all of it",
          described["set_here_count"] < described["properties_in_all"] / 10,
          True)
    check("with the margin in millimetres",
          described["set_here"]["ParaBottomMargin"]["value"], "2.47 mm")
    check("and the raw hundredths kept",
          described["set_here"]["ParaBottomMargin"]["raw"], 247)
    check("the line spacing as a percentage",
          described["set_here"]["ParaLineSpacing"]["value"], "115%")
    check("its struct as fields, not as a repr",
          described["set_here"]["ParaLineSpacing"]["raw"],
          {"mode": "proportional", "height": 115})

    check("the font is in force but inherited",
          (described["effective"]["CharFontName"]["value"],
           described["effective"]["CharFontName"]["from"]),
          ("Liberation Serif", "inherited"))
    check("the spacing is in force from this style",
          described["effective"]["ParaLineSpacing"]["from"], "this style")
    check("the size reads in points",
          described["effective"]["CharHeight"]["value"], "12.0 pt")

    print("\n   the same style, found from the text instead of named:")
    from_text = bridge.describe_style(address={"paragraph": 1}, doc=doc)
    print("      ¶1 uses", from_text["style"]["name"])
    check("a style can be found from an address",
          from_text.get("success"), True)
    heading = bridge.describe_style(address={"paragraph": 0}, doc=doc)
    check("and a heading is recognised as its own style",
          heading["style"]["name"].startswith("Heading"), True)
    check("with an outline level in force",
          heading["effective"]["OutlineLevel"]["value"] > 0, True)

    check("an unknown style is refused",
          bridge.describe_style("Nope", doc=doc).get("success"), False)
    check("an unknown family is refused",
          bridge.describe_style("Text body", family="chair",
                                doc=doc).get("success"), False)
    everything = bridge.describe_style("Text body", all_properties=True,
                                       doc=doc)
    check("all_properties returns the lot with their states",
          len(everything["all_properties"]),
          everything["properties_in_all"])
    check("and the states tell own from inherited",
          (everything["all_properties"]["ParaBottomMargin"]["state"],
           everything["all_properties"]["CharFontName"]["state"]),
          ("DIRECT_VALUE", "DEFAULT_VALUE"))
    check("a character style can be described too",
          bridge.describe_style("Source Text", family="character",
                                doc=doc).get("success"), True)

    print("\n--- a selected picture is not a text selection ---")
    picture_file = write_test_png(tmp_path("mcp_live_source.png"))   # the earlier
    plain = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    plain.gotoEndOfParagraph(True)
    plain.setString("query is the entry point")
    put_picture(6, "Chosen", title="the chosen one", description="the selected one")
    chosen = doc.getGraphicObjects().getByName("Chosen")
    doc.getCurrentController().select(chosen)

    selection = doc.getCurrentController().getSelection()
    print("   the selection is:", selection.supportsService(
        "com.sun.star.text.TextGraphicObject") and "the picture itself"
        or "something else")
    check("Writer hands back the picture, not a range",
          selection.supportsService("com.sun.star.text.TextGraphicObject"), True)

    listed = bridge.list_images({"selection": True}, doc=doc)
    print(listed)
    check("list_images says which picture is selected",
          [image["name"] for image in listed["images"]], ["Chosen"])
    check("and says that is what the scope was", listed.get("scope"),
          {"selection": "picture"})

    reported = bridge.get_cursor_info(doc=doc)
    print({k: v for k, v in reported.items() if k != "images"})
    check("the cursor report says a picture is selected",
          reported.get("selection_kind"), "picture")
    check("naming it", [image["name"] for image in reported["images"]],
          ["Chosen"])
    check("with the text it is anchored to",
          reported["images"][0]["paragraph_text"], "query is the entry point")

    saved = bridge.export_image(path=tmp_path("mcp_live_selected.png"), doc=doc)
    print({k: v for k, v in saved.items() if k != "_image_content"})
    check("the selected picture writes out without being named",
          (saved.get("success"), saved.get("name"), saved.get("was_selected")),
          (True, "Chosen", True))
    check("as a PNG", open(saved["path"], "rb").read(8),
          b"\x89PNG\r\n\x1a\x0a")
    os.unlink(saved["path"])

    refused = bridge.replace_selection("translation", doc=doc)
    print(refused)
    check("a text tool says what is really selected",
          "a picture is selected" in refused.get("error", ""), True)
    check("and names it", "Chosen" in refused["error"], True)

    # put a text selection back, so the checks that follow have one
    doc.getCurrentController().select(plain)
    chosen.getAnchor().getText().removeTextContent(chosen)
    check("the chosen picture is gone again",
          [image["name"] for image in bridge.list_images(doc=doc)["images"]
           if image["name"] == "Chosen"], [])

    print("\n--- rendering a page, with LibreOffice alone ---")
    # Enough text for a second page, so page selection can be checked.
    tail = body.createTextCursorByRange(body.getEnd())
    for _ in range(220):
        body.insertString(tail, "Filling the page so that a second one exists. ",
                          False)
    view = doc.getCurrentController().getViewCursor()
    view.jumpToPage(1)

    pages = doc.getRendererCount(doc, ())
    print("   pages:", pages)
    check("the document has more than one page", pages > 1, True)

    first = bridge.render_page(page=1, dpi=90, path=tmp_path("mcp_live_page1.png"),
                               inline=False, doc=doc)
    print({k: v for k, v in first.items() if k != "_image_content"})
    check("rendered", first.get("success"), True)
    check("by LibreOffice's own filter", first.get("rendered_by"),
          "writer_png_Export")
    check("as a PNG", open(first["path"], "rb").read(8), b"\x89PNG\r\n\x1a\x0a")
    check("of the size it reports", first.get("bytes"),
          os.path.getsize(first["path"]))
    check("with the pixels of an A4 page at 90 dpi", first.get("pixels"),
          {"width": 744, "height": 1052})
    check("saying what it does not show",
          "spell checker" in first["shows"], True)

    second = bridge.render_page(page=2, dpi=90, path=tmp_path("mcp_live_page2.png"),
                                inline=False, doc=doc)
    check("the second page rendered too", second.get("success"), True)
    check("and it is a different page",
          open(tmp_path("mcp_live_page1.png"), "rb").read()
          != open(tmp_path("mcp_live_page2.png"), "rb").read(), True)
    check("the reader's cursor is back on page 1", view.getPage(), 1)

    by_address = bridge.render_page(address={"paragraph": 3}, dpi=60,
                                    path=tmp_path("mcp_live_addr.png"),
                                    inline=False, doc=doc)
    print({k: v for k, v in by_address.items() if k != "_image_content"})
    check("a page found from an address", by_address.get("success"), True)
    check("which page ¶3 is on", by_address.get("page"), 1)

    looked_at = bridge.render_page(dpi=60, inline=True, doc=doc)
    check("the page the reader is on, handed back",
          (looked_at.get("page"), looked_at.get("inline")), (1, True))
    check("as base64 of a PNG",
          looked_at["_image_content"]["data"].startswith("iVBORw0KGgo"), True)
    os.unlink(looked_at["path"])

    check("a page the document has not is refused",
          bridge.render_page(page=pages + 5, doc=doc).get("success"), False)
    check("a silly resolution is refused",
          bridge.render_page(page=1, dpi=5000, doc=doc).get("success"), False)

    print("\n--- and the same page through the PDF route ---")
    through_draw = bridge._render_through_draw(doc, 2, tmp_path("mcp_live_draw.png"),
                                               744, 1052)
    check("the fallback wrote a picture too", through_draw is not None, True)
    if through_draw:
        check("as a PNG", open(through_draw, "rb").read(8),
              b"\x89PNG\r\n\x1a\x0a")
        os.unlink(through_draw)
    for leftover in (tmp_path("mcp_live_page1.png"), tmp_path("mcp_live_page2.png"),
                     tmp_path("mcp_live_addr.png")):
        os.unlink(leftover)

    print("\n--- an anchor keeps pointing while the paragraphs move ---")
    # Paragraphs of this section's own, so nothing here depends on what ran
    # before it.
    marker = body.createTextCursorByRange(body.getEnd())
    for line in ("ANCHOR-ABOVE", "ANCHOR-ONE", "ANCHOR-TWO"):
        body.insertControlCharacter(marker, PARAGRAPH_BREAK, False)
        body.insertString(marker, line, False)
    total = bridge.read_paragraphs(start=0, count=1, doc=doc)["total_paragraphs"]
    above, first, second = total - 3, total - 2, total - 1

    held = bridge.anchor([{"paragraph": first}, {"paragraph": second}], doc=doc)
    print("   ", held)
    check("both places anchored", held.get("held"), 2)
    one, two = [entry["address"]["anchor"]["anchorId"]
                for entry in held["anchors"]]
    check("and each says what it holds",
          [entry["text"] for entry in held["anchors"]],
          ["ANCHOR-ONE", "ANCHOR-TWO"])

    body.removeTextContent(bridge._paragraph_at(body, above))
    check("the index it was found at now names the next paragraph",
          bridge._resolve_address(doc, {"paragraph": first}).getString(),
          "ANCHOR-TWO")
    check("the anchor still holds its own text",
          bridge._resolve_address(doc, {"anchor": one}).getString(),
          "ANCHOR-ONE")
    listed = bridge.list_anchors(number=True, doc=doc)
    moved = [entry for entry in listed["anchors"]
             if entry["anchor"]["anchorId"] == one][0]
    check("and reports where it has moved to",
          moved["address"]["paragraph"], first - 1)

    written = bridge.replace_range({"anchor": one}, "ANCHOR-REWRITTEN", doc=doc)
    check("an anchor is an address a replacement takes",
          written.get("success"), True)
    check("and it goes on pointing at what it replaced with",
          bridge._resolve_address(doc, {"anchor": one}).getString(),
          "ANCHOR-REWRITTEN")

    bridge.replace_range({"paragraph": second - 1}, "REWRITTEN PAST IT", doc=doc)
    stale = False
    try:
        bridge._resolve_address(doc, {"anchor": two})
    except AddressError as e:
        stale = True
        print("   refused:", e)
    check("an anchor whose text was rewritten past it refuses", stale, True)
    dead = [entry for entry in bridge.list_anchors(doc=doc)["anchors"]
            if entry["anchor"]["anchorId"] == two][0]
    check("and is listed as dead, saying what it held",
          (dead["alive"], dead["held_when_made"]), (False, "ANCHOR-TWO"))

    # The listing places every anchor it reports, and placing one used to
    # reach its paragraph by number: a walk of the body apiece, which on a
    # real document with hundreds held never came back at all. One walk now
    # serves the whole call, and the paragraphs it reports have to be right
    # after the numbers move, which is what the halving search is for.
    read_back = bridge.read_paragraphs(start=0, count=20, doc=doc)["paragraphs"]
    where = {entry["address"]["anchor"]["anchorId"]: entry["paragraph"]
             for entry in read_back}
    listed = bridge.list_anchors(count=200, number=True, doc=doc)
    placed = {entry["anchor"]["anchorId"]:
              (entry.get("address") or {}).get("paragraph")
              for entry in listed["anchors"]}
    check("every anchor a read handed out is placed where the read found it",
          all(placed.get(token) == index for token, index in where.items()),
          True)
    text = doc.getText()
    moved_up = text.createTextCursorByRange(text.getStart())
    text.insertString(moved_up, "Pushed down.", False)
    text.insertControlCharacter(moved_up, PARAGRAPH_BREAK, False)
    after = {entry["anchor"]["anchorId"]:
             (entry.get("address") or {}).get("paragraph")
             for entry in bridge.list_anchors(count=200, number=True,
                                              doc=doc)["anchors"]}
    check("and each is one further down once a paragraph is put above them",
          all(after.get(token) == index + 1 for token, index in where.items()),
          True)
    text.removeTextContent(bridge._paragraph_at(text, 0))
    paged = bridge.list_anchors(count=1, doc=doc)
    check("the listing is paged, and says how many are held in all",
          (paged["count"], paged["more"], paged["held"] >= 2), (1, True, True))

    hits = bridge.find_text("ANCHOR-REWRITTEN", anchors=True, doc=doc)
    check("a search can hand back an anchor per hit",
          bridge._resolve_address(
              doc, hits["hits"][0]["address"]).getString(),
          "ANCHOR-REWRITTEN")

    table.getCellByName("A1").setString("ANCHOR-IN-CELL")
    in_cell = bridge.anchor({"table": table_name, "cell": "A1"}, doc=doc)
    check("a cell can be anchored too",
          bridge._resolve_address(
              doc, in_cell["anchors"][0]["address"]).getString(),
          "ANCHOR-IN-CELL")

    # A cursor into a cell whose table is taken away is disposed, not merely
    # emptied: it throws, and the refusal has to say so rather than pass the
    # exception on.
    doomed = doc.createInstance("com.sun.star.text.TextTable")
    doomed.initialize(1, 1)
    doomed.setName("AnchorTable")
    end = body.createTextCursorByRange(body.getEnd())
    body.insertTextContent(end, doomed, False)
    doomed.getCellByName("A1").setString("WILL-VANISH")
    gone = bridge.anchor({"table": "AnchorTable", "cell": "A1"},
                         doc=doc)["anchors"][0]["address"]["anchor"]["anchorId"]
    body.removeTextContent(doomed)
    refused = None
    try:
        bridge._resolve_address(doc, {"anchor": gone})
    except AddressError as e:
        refused = str(e)
    check("an anchor in a table that is gone refuses rather than throwing",
          refused is not None and "is gone" in refused, True)
    check("and list_anchors says the same without raising",
          [entry["alive"] for entry in bridge.list_anchors(number=True,
                                                           doc=doc)["anchors"]
           if entry["anchor"]["anchorId"] == gone], [False])

    check("anchors are let go when asked",
          bridge.drop_anchors(doc=doc).get("count") >= 3, True)
    check("and then the token means nothing",
          _refused(bridge, doc, {"anchor": one}), True)

    for _ in range(2):                      # take this section's paragraphs away
        last = bridge._paragraph_at(body, bridge.read_paragraphs(
            start=0, count=1, doc=doc)["total_paragraphs"] - 1)
        body.removeTextContent(last)

    print("\n--- bookmarks: the names a document keeps for places ---")
    marker = body.createTextCursorByRange(body.getEnd())
    body.insertControlCharacter(marker, PARAGRAPH_BREAK, False)
    body.insertString(marker, "МЕТКА-СТРОКА для закладок", False)
    where = bridge.read_paragraphs(start=0, count=1,
                                   doc=doc)["total_paragraphs"] - 1

    made = bridge.add_bookmark({"paragraph": where, "offset": 0, "length": 12},
                               "Метка", doc=doc)
    print("   ", {key: made.get(key) for key in
                  ("success", "name", "text", "is_a_point")})
    check("a bookmark covers the text it was put on",
          (made.get("success"), made.get("text")), (True, "МЕТКА-СТРОКА"))
    point = bridge.add_bookmark({"paragraph": where, "offset": 13,
                                 "length": 0}, "Точка", doc=doc)
    check("and one at a caret marks a spot", point.get("is_a_point"), True)
    check("a name that is taken is refused",
          bridge.add_bookmark({"paragraph": where}, "Метка",
                              doc=doc).get("code"), "INVALID_PARAMETER")

    listed = bridge.list_bookmarks({"paragraph": where}, number=True, doc=doc)
    check("both are listed, in the order they sit in",
          [one["name"] for one in listed["bookmarks"]], ["Метка", "Точка"])
    check("each with the address of what it covers",
          listed["bookmarks"][0]["address"]["length"], 12)
    # Placing them by number is a sweep of the body; the anchors are not.
    fast = bridge.list_bookmarks({"paragraph": where}, doc=doc)
    check("and the fast listing places them by anchor alone",
          ([one["address"]["anchor"]["type"] for one in fast["bookmarks"]],
           all("paragraph" not in one["address"]
               for one in fast["bookmarks"])),
          (["text", "text"], True))
    check("each anchor resolving to what its bookmark covers",
          [bridge._resolve_address(doc, one["address"]).getString()
           for one in fast["bookmarks"]],
          [one["text"] for one in fast["bookmarks"]])

    check("renaming leaves it where it is",
          bridge.rename_bookmark("Метка", "Метка-2", doc=doc).get("success"),
          True)
    check("under the new name",
          [one["name"] for one in bridge.list_bookmarks(
              {"paragraph": where}, doc=doc)["bookmarks"]],
          ["Метка-2", "Точка"])

    print("\n   a bookmark outlives what would take a comment away:")
    bridge.replace_range({"paragraph": where, "offset": 0, "length": 12},
                         "ПЕРЕПИСАНО", flatten=True, doc=doc)
    after = bridge.list_bookmarks({"paragraph": where}, doc=doc)
    check("the rewrite left the bookmark in the document",
          "Метка-2" in [one["name"] for one in after["bookmarks"]], True)

    gone = bridge.delete_bookmark("Метка-2", doc=doc)
    check("and it can be taken away", gone.get("success"), True)
    check("leaving the text",
          "ПЕРЕПИСАНО" in bridge.read_paragraphs(
              start=where, count=1, doc=doc)["paragraphs"][0]["text"], True)
    check("a bookmark that is not there",
          bridge.delete_bookmark("Нетакой", doc=doc).get("code"), "NOT_FOUND")
    bridge.delete_bookmark("Точка", doc=doc)
    body.removeTextContent(bridge._paragraph_at(body, where))

    print("\n--- fields: the bits that write themselves ---")
    marker = body.createTextCursorByRange(body.getEnd())
    body.insertControlCharacter(marker, PARAGRAPH_BREAK, False)
    body.insertString(marker, "Страница X из Y, составлено Z", False)
    page = bridge.read_paragraphs(start=0, count=1,
                                  doc=doc)["total_paragraphs"] - 1

    put = bridge.insert_field({"paragraph": page, "offset": 9, "length": 1},
                              "page_number", doc=doc)
    print("   ", {key: put.get(key) for key in
                  ("success", "kind", "shows", "command")})
    check("a page number went in", put.get("success"), True)
    check("showing a page number", (put.get("shows") or "").isdigit(), True)
    line = bridge.read_paragraphs(start=page, count=1,
                                  doc=doc)["paragraphs"][0]["text"]
    check("in place of the text it replaced", "из Y" in line, True)

    bridge.insert_field({"paragraph": page, "offset": len(line) - 5,
                         "length": 1}, "page_count", doc=doc)
    bridge.insert_field({"paragraph": page,
                         "offset": len(bridge.read_paragraphs(
                             start=page, count=1,
                             doc=doc)["paragraphs"][0]["text"]) - 1,
                         "length": 1}, "date", doc=doc)
    refreshed_first = bridge.update_fields(doc=doc)
    check("fields can be made to redraw", refreshed_first.get("success"), True)
    listed = bridge.list_fields({"paragraph": page}, number=True, doc=doc)
    print("   ", [(one["kind"], one["text"], one["address"]["offset"])
                  for one in listed["fields"]])
    check("all three are listed", listed["count"], 3)
    check("each knowing what it is",
          sorted(one["kind"] for one in listed["fields"]),
          ["date", "page_count", "page_number"])
    check("and what Writer calls it",
          all(one["command"] for one in listed["fields"]), True)
    check("the comments are not listed as fields",
          all(one["kind"] is not None for one in listed["fields"]), True)

    print("\n   a field is not ordinary text:")
    runs = bridge.read_runs({"paragraph": page}, doc=doc)
    carrying = [run for run in runs["runs"] if run.get("field")]
    check("read_runs says which run is a field", len(carrying), 3)
    check("naming what it shows",
          carrying[0]["field"]["text"], carrying[0]["text"])
    refused = bridge.replace_range({"paragraph": page}, "переписано", doc=doc)
    print("   ", refused.get("error"))
    check("a rewrite over them is refused",
          (refused.get("success"), refused.get("code")),
          (False, "WOULD_LOSE_FORMATTING"))
    check("and says how many fields would go",
          "3 fields" in (refused.get("error") or ""), True)

    print("\n   taking one away:")
    date_at = [one for one in listed["fields"] if one["kind"] == "date"][0]
    gone = bridge.delete_field(date_at["address"], doc=doc)
    print("   ", {key: gone.get(key) for key in ("success", "deleted",
                                                 "was_showing")})
    check("the date is gone", gone.get("deleted"), "date")
    check("two fields left", bridge.list_fields({"paragraph": page},
                                                doc=doc)["count"], 2)
    check("an address with no field in it is refused",
          bridge.delete_field({"paragraph": page, "offset": 0, "length": 1},
                              doc=doc).get("code"), "NOT_FOUND")
    check("and one covering several",
          bridge.delete_field({"paragraph": page}, doc=doc).get("code"),
          "INVALID_PARAMETER")
    check("a kind nobody has heard of",
          bridge.insert_field({"paragraph": page}, "weather",
                              doc=doc).get("code"), "INVALID_PARAMETER")

    flat = bridge.replace_range({"paragraph": page}, "переписано",
                                flatten=True, doc=doc)
    check("flatten writes over them and says how many went",
          (flat.get("success"), flat.get("fields_dropped")), (True, 2))
    check("and they are gone",
          bridge.list_fields({"paragraph": page}, doc=doc)["count"], 0)
    body.removeTextContent(bridge._paragraph_at(body, page))

    print("\n--- captions, and the references that follow them ---")
    # On a document of its own: captions add paragraphs between the ones the
    # sections above count, and a reference numbers itself against what is
    # already in the document.
    numbered = desktop.loadComponentFromURL("private:factory/swriter",
                                            "_blank", 0, ())
    pages = numbered.getText()
    writing = pages.createTextCursor()
    for style, line in (("Heading 1", "Scalar types"),
                        ("Standard", "A picture sits here"),
                        ("Standard", "Body text"),
                        ("Standard", "See also")):
        writing.ParaStyleName = style
        pages.insertString(writing, line, False)
        pages.insertControlCharacter(writing, PARAGRAPH_BREAK, False)
    grid = numbered.createInstance("com.sun.star.text.TextTable")
    grid.initialize(2, 2)
    grid.setName("Сетка")
    pages.insertTextContent(bridge._resolve_address(
        numbered, {"paragraph": 3}).getStart(), grid, False)

    first = bridge.insert_caption("The first picture", address={"paragraph": 1},
                                  doc=numbered)
    print("   ", {key: first.get(key) for key in
                  ("success", "category", "number", "address", "text")})
    check("a caption goes below its paragraph",
          (first.get("success"), first.get("number"), first.get("address")),
          (True, "1", {"paragraph": 2}))
    check("wearing the style named after its category",
          first.get("paragraph_style"), "Figure")
    check("and reading as one line", first.get("text"),
          "Figure 1: The first picture")

    below = bridge.insert_caption("The grid", table="Сетка", category="Table",
                                  doc=numbered)
    check("a table takes one below it",
          (below.get("number"), below.get("address")), ("1", {"paragraph": 4}))
    above = bridge.insert_caption("Above the grid", table="Сетка",
                                  category="Table", position="above",
                                  doc=numbered)
    check("or above it, which lands before the table rather than after",
          above.get("address"), {"paragraph": 4})
    check("and the one below is renumbered, since the number counts itself",
          [one["number"] for one in bridge._captions(numbered)
           if one["category"] == "Table"], ["1", "2"])

    own = bridge.insert_caption("Схема запроса", address={"paragraph": 0},
                                category="Листинг", doc=numbered)
    check("a category the document never had is made",
          (own.get("success"), own.get("category"), own.get("number")),
          (True, "Листинг", "1"))
    check("and falls back to the Caption style", own.get("paragraph_style"),
          "Caption")
    check("numbering can be roman",
          bridge.insert_caption("Roman", address={"paragraph": 0},
                                category="Drawing", numbering="roman_upper",
                                doc=numbered).get("number"), "I")
    check("a table nobody has",
          bridge.insert_caption("nowhere", table="Нетакой",
                                doc=numbered).get("code"), "INVALID_ADDRESS")
    check("naming two things to caption at once",
          bridge.insert_caption("two", address={"paragraph": 0}, table="Сетка",
                                doc=numbered).get("code"),
          "INVALID_PARAMETER")

    print("\n   what a reference can point at:")
    targets = bridge.list_reference_targets(doc=numbered)
    check("the heading is a target",
          [one["text"] for one in targets["targets"]
           if one["kind"] == "heading"], ["Scalar types"])
    check("and every caption",
          sorted(one["reference"]["caption"] for one in targets["targets"]
                 if one["kind"] == "caption"),
          sorted(["Drawing I", "Figure 1", "Table 1", "Table 2",
                  "Листинг 1"]))
    check("a kind nobody knows",
          bridge.list_reference_targets(kinds=["nonsense"],
                                        doc=numbered).get("code"),
          "INVALID_PARAMETER")

    print("\n   writing the references:")
    tail_paragraph = bridge.find_text("See also", doc=numbered)["hits"][0]
    spot = {"paragraph": tail_paragraph["address"]["paragraph"],
            "offset": 0, "length": 0}
    to_caption = bridge.insert_cross_reference(spot, {"caption": "Figure 1"},
                                               doc=numbered)
    print("   ", {key: to_caption.get(key) for key in
                  ("success", "shows", "part")})
    check("a reference to a caption shows its number",
          (to_caption.get("success"), to_caption.get("shows")),
          (True, "Figure 1"))
    check("or its words",
          bridge.insert_cross_reference(spot, {"caption": "Figure 1"},
                                        part="caption_text",
                                        doc=numbered).get("shows"),
          "The first picture")
    heading = bridge.insert_cross_reference(spot, {"heading": 0},
                                            doc=numbered)
    check("a reference to a heading shows the heading",
          (heading.get("success"), heading.get("shows")),
          (True, "Scalar types"))
    # Writer's own heading marks are named __RefHeading__…, and a bookmark
    # given that name becomes one: it disappears from getBookmarks() and a
    # second on the same heading throws. So an ordinary bookmark is left.
    check("leaving an ordinary bookmark named after it",
          heading["target"]["name"], "Scalar types")
    check("which a second reference reuses",
          bridge.insert_cross_reference(spot, {"heading": 0},
                                        doc=numbered)["target"]["name"],
          "Scalar types")
    check("ordinary text is not a heading",
          bridge.insert_cross_reference(spot, {"heading": 2},
                                        doc=numbered).get("code"),
          "INVALID_PARAMETER")
    check("a caption that is not there",
          bridge.insert_cross_reference(spot, {"caption": "Figure 9"},
                                        doc=numbered).get("code"),
          "NOT_FOUND")

    body_paragraph = bridge.find_text("Body text",
                                      doc=numbered)["hits"][0]["address"]
    bridge.add_bookmark({"paragraph": body_paragraph["paragraph"],
                         "offset": 0, "length": 4}, "Место", doc=numbered)
    check("a reference to a bookmark shows what it covers",
          bridge.insert_cross_reference(spot, {"bookmark": "Место"},
                                        doc=numbered).get("shows"), "Body")

    listed = bridge.list_references(doc=numbered)
    check("every reference is listed", (listed["count"], listed["broken"]),
          (5, 0))
    check("each saying what it points at",
          sorted({one["target"]["kind"] for one in listed["references"]}),
          ["bookmark", "caption"])

    bridge.delete_bookmark("Место", doc=numbered)
    after_delete = bridge.list_references(doc=numbered)
    check("a reference whose target went is reported broken",
          after_delete["broken"], 1)
    check("by name", [one["target"]["name"] for one in
                      after_delete["references"] if one["broken"]], ["Место"])

    print("\n   a caption put in front renumbers what follows:")
    was = [one["shows"] for one in bridge.list_references(doc=numbered)
           ["references"] if one["part"] == "category_and_number"]
    bridge.insert_caption("Inserted first", address={"paragraph": 0},
                          position="above", doc=numbered)
    now = [one["shows"] for one in bridge.list_references(doc=numbered)
           ["references"] if one["part"] == "category_and_number"]
    check("and the reference follows it", (was, now),
          (["Figure 1"], ["Figure 2"]))
    numbered.setModified(False)
    numbered.close(True)

    print("\n--- undo and redo, and text into a table and back ---")
    stepped = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                           0, ())
    stepped_text = stepped.getText()
    pen = stepped_text.createTextCursor()
    for line in ("Имя\tВозраст", "Аня\t31", "Борис\t45", "После таблицы"):
        stepped_text.insertString(pen, line, False)
        stepped_text.insertControlCharacter(pen, PARAGRAPH_BREAK, False)

    def wording():
        return [one["text"] for one in
                bridge.read_paragraphs(start=0, count=20,
                                       doc=stepped)["paragraphs"]]

    bridge.replace_range({"paragraph": 3}, "ПОСЛЕ ТАБЛИЦЫ", flatten=True,
                         doc=stepped)
    listed = bridge.list_undo_steps(doc=stepped)
    print("   ", [(one["title"], one["made_here"])
                  for one in listed["undo"][:4]])
    # The stack is the document's: the reader's typing is in it too, and the
    # titles are what tell them apart.
    check("the history says whose each step is",
          (listed["undo"][0]["made_here"], listed["ours_on_top"],
           any(not one["made_here"] for one in listed["undo"])),
          (True, True, True))
    check("with nothing to redo yet", listed["can_redo"], False)

    back = bridge.undo(doc=stepped)
    print("   ", {key: back.get(key) for key in ("undone", "steps", "next")})
    check("an edit goes back",
          (back.get("success"), wording()[3]), (True, "После таблицы"))
    check("and comes again",
          (bridge.redo(doc=stepped).get("steps"), wording()[3]),
          (1, "ПОСЛЕ ТАБЛИЦЫ"))

    bridge.undo(doc=stepped)
    refused = bridge.undo(doc=stepped)
    print("   ", refused.get("error"))
    check("and it stops at a step it did not make",
          (refused.get("success"), refused.get("code")),
          (False, "INVALID_PARAMETER"))
    check("unless it is told to",
          bridge.undo(include_others=True, doc=stepped).get("steps"), 1)
    bridge.redo(steps=2, include_others=True, doc=stepped)
    check("a silly number of steps",
          bridge.undo(steps=0, doc=stepped).get("code"), "INVALID_PARAMETER")

    print("\n   text into a table:")
    made = bridge.convert_text_to_table({"paragraph": 0, "through": 2},
                                        doc=stepped)
    print("   ", {key: made.get(key) for key in
                  ("success", "table", "rows", "columns", "first_row")})
    # Measured: convertToTable hands every character between the first cell
    # and the last to some cell, so the separators come out first or they
    # turn up at the head of the next one.
    check("the paragraphs become a table with clean cells",
          (made.get("success"), made.get("rows"), made.get("columns"),
           made.get("first_row")), (True, 3, 2, ["Имя", "Возраст"]))
    grid = bridge._table_by_name(stepped, made["table"])
    check("and every cell came through",
          [grid.getCellByName(name).getString()
           for name in ("A2", "B2", "A3", "B3")], ["Аня", "31", "Борис", "45"])
    check("the text after it is untouched", wording()[-2], "ПОСЛЕ ТАБЛИЦЫ")

    # The way back is Writer's own command, and its Delimiter must be a
    # string: with the tab's number it did nothing at all, silently.
    returned = bridge.convert_table_to_text(made["table"], doc=stepped)
    print("   ", {key: returned.get(key) for key in
                  ("success", "gone", "rows", "separator")})
    check("and the table goes back to text",
          (returned.get("success"), returned.get("gone"), wording()[:3]),
          (True, True, ["Имя\tВозраст", "Аня\t31", "Борис\t45"]))
    check("a table nobody has",
          bridge.convert_table_to_text("Нетакой", doc=stepped).get("code"),
          "NOT_FOUND")
    check("rows that do not match",
          bridge.convert_text_to_table({"paragraph": 0, "through": 3},
                                       doc=stepped).get("code"),
          "INVALID_PARAMETER")
    stepped.setModified(False)
    stepped.close(True)

    print("\n--- writing styles: making, changing, replacing ---")
    booked = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                          0, ())
    booked_text = booked.getText()
    nib = booked_text.createTextCursor()
    for style, line in (("Heading 1", "Заголовок"),
                        ("Preformatted Text", "query { hero }"),
                        ("Standard", "Обычный"),
                        ("Preformatted Text", "mutation { }")):
        nib.ParaStyleName = style
        booked_text.insertString(nib, line, False)
        booked_text.insertControlCharacter(nib, PARAGRAPH_BREAK, False)

    made = bridge.create_style("Наш код", based_on="Preformatted Text",
                               properties={"color": "#006600",
                                           "font_size": 10,
                                           "space_above_pt": 6},
                               doc=booked)
    print("   ", {key: made.get(key) for key in
                  ("success", "name", "based_on", "set")})
    check("a style is made with what it was given",
          (made.get("success"), made.get("based_on"), sorted(made.get("set"))),
          (True, "Preformatted Text", ["color", "font_size",
                                       "space_above_pt"]))
    check("and its own definition says so",
          bridge.describe_style(name="Наш код",
                                doc=booked)["set_here"]["CharColor"]["value"],
          "#006600")
    clone = bridge.create_style("Копия кода", from_style="Наш код",
                                properties={"color": "#CC0000"}, doc=booked)
    check("a style can be cloned and then changed",
          (clone.get("copied_properties") > 0,
           bridge.describe_style(name="Копия кода",
                                 doc=booked)["set_here"]["CharColor"]["value"]),
          (True, "#CC0000"))
    # A bad property used to be found after insertByName, leaving the style.
    refused = bridge.create_style("Третий", properties={"sparkle": True},
                                  doc=booked)
    check("a refused property leaves no style behind",
          (refused.get("code"),
           bridge.describe_style(name="Третий", doc=booked).get("code")),
          ("INVALID_PARAMETER", "NOT_FOUND"))

    bridge.apply_paragraph_style({"paragraph": 1}, "Наш код", doc=booked)
    bridge.update_style("Наш код", properties={"italic": True,
                                               "alignment": "center"},
                        doc=booked)
    wearing = bridge._paragraph_at(booked.getText(), 1)
    check("changing a style changes what wears it",
          (wearing.ParaAdjust, wearing.CharPosture.value), (3, "ITALIC"))
    check("a built-in style can be changed too",
          bridge.update_style("Preformatted Text",
                              properties={"space_below_pt": 3},
                              doc=booked).get("success"), True)

    bridge.rename_style("Наш код", "Код дома", doc=booked)
    check("renaming carries the text with it",
          bridge._paragraph_at(booked.getText(), 1).ParaStyleName, "Код дома")
    check("a built-in style keeps the name it is known by",
          bridge.rename_style("Standard", "Наш обычный",
                              doc=booked).get("code"), "INVALID_PARAMETER")

    # A code block inside a table cell is not a body paragraph, and
    # "throughout" has to reach it: on a real document the body held 162 of
    # them and Writer's own search found 184.
    grid = booked.createInstance("com.sun.star.text.TextTable")
    grid.initialize(1, 1)
    grid.setName("Сетка")
    booked_text.insertTextContent(bridge._resolve_address(
        booked, {"paragraph": 2}).getStart(), grid, False)
    cell = grid.getCellByName("A1")
    cell.setString("query in a cell")
    cell.createEnumeration().nextElement().ParaStyleName = "Preformatted Text"

    swapped = bridge.replace_style("Preformatted Text", "Код дома",
                                   doc=booked)
    print("   ", swapped)
    check("one style takes the place of another",
          (swapped.get("places_changed") >= 3,
           bridge._paragraph_at(booked.getText(), 4).ParaStyleName),
          (True, "Код дома"))
    check("reaching inside a table cell as well",
          cell.createEnumeration().nextElement().ParaStyleName, "Код дома")

    # Measured: UNO accepts removing a built-in style, removes nothing and
    # says nothing, so this refuses rather than reporting it done.
    check("removing a built-in style is refused",
          bridge.delete_style("Preformatted Text", doc=booked).get("code"),
          "INVALID_PARAMETER")
    removed = bridge.delete_style("Код дома", replace_with="Preformatted Text",
                                  doc=booked)
    print("   ", removed)
    check("and one of our own goes, moving its text first",
          (removed.get("success"),
           bridge._paragraph_at(booked.getText(), 1).ParaStyleName),
          (True, "Preformatted Text"))
    booked.setModified(False)
    booked.close(True)

    print("\n--- finding by style, and the formatting over one ---")
    styled = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                          0, ())
    styled_text = styled.getText()
    quill = styled_text.createTextCursor()
    for style, line in (("Heading 1", "Заголовок"),
                        ("Standard", "Обычный абзац тут"),
                        ("Preformatted Text", "query { hero }"),
                        ("Standard", "Ещё обычный"),
                        ("Preformatted Text", "mutation { }")):
        quill.ParaStyleName = style
        styled_text.insertString(quill, line, False)
        styled_text.insertControlCharacter(quill, PARAGRAPH_BREAK, False)
    bridge.format_range({"paragraph": 1, "offset": 0, "length": 7}, bold=True,
                        color="#CC0000", doc=styled)
    bridge.format_range({"paragraph": 1, "offset": 8, "length": 5},
                        character_style="Emphasis", doc=styled)
    bridge.format_range({"paragraph": 3, "offset": 0, "length": 4},
                        link="https://example.org/", doc=styled)

    started = time.time()
    found = bridge.find_by_style("Preformatted Text", doc=styled)
    took = time.time() - started
    print("   ", [(hit["address"], hit["text"]) for hit in found["hits"]])
    # Writer's own search takes a paragraph style's name when SearchStyles is
    # on, and answers in milliseconds where a walk would compare every
    # paragraph.
    check("the code blocks are found by their style",
          [hit["text"] for hit in found["hits"] if hit["text"]],
          ["query { hero }", "mutation { }"])
    check("and quickly", took < 0.5, True)
    check("a character style is found by walking the runs",
          [(hit["text"], hit["address"]["offset"]) for hit in
           bridge.find_by_style("Emphasis", family="character",
                                doc=styled)["hits"]], [("абзац", 8)])
    check("a style nobody has",
          bridge.find_by_style("Нетакой", doc=styled).get("code"), "NOT_FOUND")

    direct = bridge.get_direct_formatting({"paragraph": 1, "offset": 0,
                                           "length": 7}, doc=styled)
    print("   ", {key: (value["value"], value["where"])
                  for key, value in direct["character"].items()})
    check("what was applied over the style is named",
          (sorted(direct["character"]), direct["character"]["color"]["value"]),
          (["bold", "color"], "#CC0000"))
    check("a paragraph wearing only its style has nothing over it",
          bridge.get_direct_formatting({"paragraph": 2},
                                       doc=styled)["character"], {})
    # Applied to a whole paragraph it lands on the paragraph, where the range
    # reports nothing at all.
    bridge.format_range({"paragraph": 4}, bold=True, doc=styled)
    whole = bridge.get_direct_formatting({"paragraph": 4}, doc=styled)
    check("formatting over a whole paragraph is seen, on the paragraph",
          (list(whole["character"]), whole["character"]["bold"]["where"]),
          (["bold"], "paragraph"))

    cleared = bridge.clear_direct_formatting({"paragraph": 1, "offset": 0,
                                              "length": 7}, doc=styled)
    print("   ", cleared.get("cleared"))
    check("it comes off again",
          (cleared.get("success"),
           sorted(cleared["cleared"]["characters"])), (True, ["bold", "color"]))
    check("and nothing is left over the style",
          bridge.get_direct_formatting({"paragraph": 1, "offset": 0,
                                        "length": 7},
                                       doc=styled)["character"], {})
    check("clearing a whole paragraph reaches the paragraph",
          ("bold" in bridge.clear_direct_formatting(
              {"paragraph": 4}, doc=styled)["cleared"]["characters"],
           bridge.get_direct_formatting({"paragraph": 4},
                                        doc=styled)["character"]),
          (True, {}))

    # Measured: neither a link nor a character style is direct formatting.
    survived = bridge.clear_direct_formatting({"paragraph": 3}, doc=styled)
    link = bridge._resolve_address(styled, {"paragraph": 3, "offset": 0,
                                            "length": 4})
    check("a hyperlink survives a clean-up",
          (link.HyperLinkURL, survived.get("links_kept")),
          ("https://example.org/", 1))
    bridge.clear_direct_formatting({"paragraph": 1, "offset": 8, "length": 5},
                                   doc=styled)
    check("and so does a character style",
          bridge._resolve_address(styled, {"paragraph": 1, "offset": 8,
                                           "length": 5}).CharStyleName,
          "Emphasis")
    check("unless it is named outright",
          bridge.clear_direct_formatting(
              {"paragraph": 1, "offset": 8, "length": 5},
              properties=["character_style"],
              doc=styled)["cleared"]["characters"], ["character_style"])
    check("clearing nothing at all",
          bridge.clear_direct_formatting({"paragraph": 1}, characters=False,
                                         doc=styled).get("code"),
          "INVALID_PARAMETER")
    styled.setModified(False)
    styled.close(True)

    print("\n--- runs over a block, and taking a colour off ---")
    blocked = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                           0, ())
    blocked_text = blocked.getText()
    pen = blocked_text.createTextCursor()
    for line in ("Первый абзац с цветом", "Второй абзац жирный",
                 "Третий абзац обычный", "Четвёртый"):
        blocked_text.insertString(pen, line, False)
        blocked_text.insertControlCharacter(pen, PARAGRAPH_BREAK, False)
    bridge.format_range({"paragraph": 0, "offset": 7, "length": 5},
                        color="#CC0000", doc=blocked)
    bridge.format_range({"paragraph": 1, "offset": 7, "length": 5}, bold=True,
                        doc=blocked)

    read = bridge.read_runs({"paragraph": 0, "through": 2}, doc=blocked)
    print("   ", [(run["text"], run["address"]["paragraph"])
                  for run in read["runs"]])
    # It used to answer with the first paragraph's runs while reporting that
    # the range spanned three, which reads as a promise not kept.
    check("a block address reads every paragraph in it",
          (sorted({run["address"]["paragraph"] for run in read["runs"]}),
           read.get("paragraphs"), read.get("paragraphs_read")),
          ([0, 1, 2], [0, 2], 3))
    check("and one paragraph answers as it always did",
          ("paragraphs" in bridge.read_runs({"paragraph": 1}, doc=blocked),
           bridge.read_runs({"paragraph": 1}, doc=blocked)["paragraph"]),
          (False, 1))

    was = [one["text"] for one in
           bridge.read_paragraphs(start=0, count=5, doc=blocked)["paragraphs"]]
    refused = bridge.replace_runs({"paragraph": 0, "through": 2},
                                  [{"text": "ОДНА СТРОКА"}], doc=blocked)
    print("   ", refused.get("error"))
    # Measured before the guard: three paragraphs in, one out, and success.
    check("writing runs over a block is refused, not silently collapsing it",
          (refused.get("success"), refused.get("code"),
           refused.get("paragraphs")),
          (False, "WOULD_LOSE_FORMATTING", [0, 2]))
    check("with the paragraphs still there",
          [one["text"] for one in bridge.read_paragraphs(
              start=0, count=5, doc=blocked)["paragraphs"]], was)

    print("\n   taking a colour off:")
    bridge.format_range({"paragraph": 3}, color="#CC0000",
                        background_color="#FFFF00", doc=blocked)
    coloured = bridge._resolve_address(blocked, {"paragraph": 3})
    check("a colour goes on", (coloured.CharColor, coloured.CharBackColor),
          (0xCC0000, 0xFFFF00))
    off = bridge.format_range({"paragraph": 3}, color="automatic",
                              background_color="auto", doc=blocked)
    print("   ", off.get("applied"))
    plain = bridge._resolve_address(blocked, {"paragraph": 3})
    # Automatic is -1 — exactly what untouched text reports — and not black.
    check("and comes off as automatic, which is what untouched text has",
          (plain.CharColor, plain.CharBackColor), (-1, -1))
    check("which the runs report as no colour at all",
          [(run.get("color"), run.get("background_color"))
           for run in bridge.read_runs({"paragraph": 3},
                                       doc=blocked)["runs"]],
          [(None, None)])
    check("a paragraph fill comes off the same way",
          bridge.format_paragraph({"paragraph": 3}, background_color="auto",
                                  doc=blocked).get("success"), True)
    check("but a border has no automatic colour",
          bridge.format_paragraph({"paragraph": 3}, border=True,
                                  border_color="automatic",
                                  doc=blocked).get("code"), "INVALID_ADDRESS")
    blocked.setModified(False)
    blocked.close(True)

    print("\n--- paragraph surgery: split, join, move, copy ---")
    cut = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                       0, ())
    cut_text = cut.getText()
    scalpel = cut_text.createTextCursor()
    for line in ("Заголовок", "Первый абзац", "Второй абзац", "Третий абзац",
                 "Четвёртый абзац"):
        cut_text.insertString(scalpel, line, False)
        cut_text.insertControlCharacter(scalpel, PARAGRAPH_BREAK, False)
    bridge.add_comment({"paragraph": 1, "offset": 0, "length": 6}, "Заметка",
                       doc=cut)
    bridge.format_range({"paragraph": 1, "offset": 7, "length": 5}, bold=True,
                        doc=cut)

    def shape():
        return [one["text"] for one in
                bridge.read_paragraphs(start=0, count=20,
                                       doc=cut)["paragraphs"]]

    split = bridge.split_paragraph({"paragraph": 2, "offset": 6}, doc=cut)
    print("   ", {key: split.get(key) for key in ("success", "split", "at")})
    check("a paragraph comes apart where it was cut",
          (split.get("success"),
           [one["text"] for one in split["paragraphs"]]),
          (True, ["Второй", " абзац"]))
    merged = bridge.merge_paragraphs({"paragraph": 2}, doc=cut)
    check("and the two go back together",
          (merged.get("joins"), merged.get("text")), (1, "Второй абзац"))
    check("a block joins into one",
          bridge.merge_paragraphs({"paragraph": 3, "through": 4},
                                  doc=cut).get("text"),
          "Третий абзацЧетвёртый абзац")
    check("the last paragraph has nothing after it to join to",
          bridge.merge_paragraphs({"paragraph": 20}, doc=cut).get("code"),
          "INVALID_ADDRESS")

    print("\n   moving, with everything on it:")
    was = shape()
    moved = bridge.move_paragraph({"paragraph": 1}, direction="down", doc=cut)
    print("   ", {key: moved.get(key) for key in ("moved", "to", "text")})
    # There is no move on the model: .uno:MoveDown is what carries a
    # paragraph with its comments and its formatting.
    check("the paragraph moved", (moved.get("success"), shape()[2]),
          (True, "Первый абзац"))
    carried = bridge.list_comments(doc=cut)
    check("its comment came with it",
          (carried["count"], carried["comments"][0]["address"]["paragraph"]),
          (1, 2))
    check("and so did the bold inside it",
          [run["bold"] for run in bridge.read_runs({"paragraph": 2},
                                                   doc=cut)["runs"]
           if run["text"] == "абзац"], [True])
    bridge.move_paragraph({"paragraph": 2}, direction="up", doc=cut)
    check("and it goes back", shape(), was)
    bridge.move_paragraph({"paragraph": 1}, to=3, doc=cut)
    check("moving to a named place", shape()[2], "Первый абзац")
    bridge.move_paragraph({"paragraph": 2}, to=1, doc=cut)
    check("and back again", shape(), was)
    check("naming none, or two ways, or nowhere to go",
          (bridge.move_paragraph({"paragraph": 1}, doc=cut).get("code"),
           bridge.move_paragraph({"paragraph": 1}, direction="up", to=3,
                                 doc=cut).get("code"),
           bridge.move_paragraph({"paragraph": 0}, direction="up",
                                 doc=cut).get("code")),
          ("INVALID_PARAMETER", "INVALID_PARAMETER", "INVALID_PARAMETER"))

    print("\n   copying, through the view rather than the clipboard:")
    copied = bridge.copy_paragraphs({"paragraph": 1}, to=4, doc=cut)
    print("   ", {key: copied.get(key) for key in
                  ("success", "copied", "to", "paragraphs")})
    check("the copy is there and so is the original",
          (shape()[4], shape()[1]), ("Первый абзац", "Первый абзац"))
    check("and the comment was copied with it",
          bridge.list_comments(doc=cut)["count"], 2)
    check("copying a block into itself",
          bridge.copy_paragraphs({"paragraph": 1}, to=1, doc=cut).get("code"),
          "INVALID_PARAMETER")
    cut.setModified(False)
    cut.close(True)

    print("\n--- hyperlinks: listing them, and taking one away ---")
    linked = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                          0, ())
    linked_text = linked.getText()
    nib = linked_text.createTextCursor()
    for style, line in (("Heading 1", "Scalar types"),
                        ("Standard",
                         "See the GraphQL specification for more."),
                        ("Standard",
                         "And the section above, and a dead end.")):
        nib.ParaStyleName = style
        linked_text.insertString(nib, line, False)
        linked_text.insertControlCharacter(nib, PARAGRAPH_BREAK, False)

    # format_range had every character property but this one, so making a
    # word into a link meant rewriting the text with replace_runs.
    made = bridge.format_range({"paragraph": 1, "offset": 8, "length": 21},
                               link="https://spec.graphql.org/",
                               link_target="_blank", doc=linked)
    check("format_range makes a link without touching the text",
          (made.get("success"),
           bridge.read_paragraphs(start=1, count=1,
                                  doc=linked)["paragraphs"][0]["text"]),
          (True, "See the GraphQL specification for more."))
    bridge.add_bookmark({"paragraph": 0}, "Скаляры", doc=linked)
    bridge.format_range({"paragraph": 2, "offset": 8, "length": 13},
                        link="#Скаляры", doc=linked)
    bridge.format_range({"paragraph": 2, "offset": 27, "length": 8},
                        link="#Нетакой", doc=linked)

    listed = bridge.list_hyperlinks(doc=linked)
    print("   ", [(one["text"], one["url"], one["broken"])
                  for one in listed["links"]])
    check("every link is listed, with what it points at",
          (listed["count"], listed["distinct_urls"], listed["internal"]),
          (3, 3, 2))
    by_url = {one["url"]: one for one in listed["links"]}
    # Nothing here reaches the network, so an http link is neither claimed
    # sound nor claimed broken.
    check("an outside link is not judged",
          by_url["https://spec.graphql.org/"]["broken"], None)
    check("an inside one is, both ways",
          (by_url["#Скаляры"]["broken"], by_url["#Нетакой"]["broken"]),
          (False, True))
    check("the text of a link is the words it sits on",
          by_url["https://spec.graphql.org/"]["text"], "GraphQL specification")

    bridge.format_range({"paragraph": 1, "offset": 8, "length": 7}, bold=True,
                        doc=linked)
    runs = bridge.read_runs({"paragraph": 1}, doc=linked)
    check("a link split into two runs is still one link",
          (len([run for run in runs["runs"] if run.get("link")]),
           bridge.list_hyperlinks({"paragraph": 1}, doc=linked)["count"]),
          (2, 1))

    removed = bridge.remove_hyperlink({"paragraph": 1}, doc=linked)
    print("   ", {key: removed.get(key) for key in ("success", "removed")})
    check("removing one leaves the words",
          (removed.get("removed"),
           bridge.read_paragraphs(start=1, count=1,
                                  doc=linked)["paragraphs"][0]["text"]),
          (1, "See the GraphQL specification for more."))
    # Clearing the URL alone leaves the blue underline: it is two character
    # styles, not a colour.
    span = bridge._resolve_address(linked, {"paragraph": 1, "offset": 8,
                                            "length": 21})
    check("and nothing of the link behind it",
          (span.HyperLinkURL, span.CharStyleName, span.UnvisitedCharStyleName),
          ("", "", ""))
    check("while the bold inside it stayed",
          [run["bold"] for run in bridge.read_runs({"paragraph": 1},
                                                   doc=linked)["runs"]
           if run["text"].startswith("GraphQL")], [True])

    check("removing by url",
          bridge.remove_hyperlink(url="#Нетакой", doc=linked).get("removed"),
          1)
    check("naming none, or two ways at once",
          (bridge.remove_hyperlink(doc=linked).get("code"),
           bridge.remove_hyperlink(url="x", all=True,
                                   doc=linked).get("code")),
          ("INVALID_PARAMETER", "INVALID_PARAMETER"))
    check("a url nobody has",
          bridge.remove_hyperlink(url="https://nowhere/",
                                  doc=linked).get("code"), "NOT_FOUND")
    check("and the rest of them at once",
          (bridge.remove_hyperlink(all=True, doc=linked).get("removed"),
           bridge.list_hyperlinks(doc=linked)["count"]), (1, 0))
    linked.setModified(False)
    linked.close(True)

    print("\n--- the page itself: size, margins, breaks, line numbers ---")
    laid = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                        0, ())
    laid_text = laid.getText()
    ruler = laid_text.createTextCursor()
    for line in ("First", "Second", "Third"):
        laid_text.insertString(ruler, line, False)
        laid_text.insertControlCharacter(ruler, PARAGRAPH_BREAK, False)

    layout = bridge.get_page_layout(doc=laid)
    print("   ", {key: layout.get(key) for key in
                  ("page_style", "width_mm", "height_mm", "paper",
                   "orientation", "margins_mm", "columns")})
    check("A4 upright, in millimetres",
          (layout.get("paper"), layout.get("orientation"),
           round(layout["width_mm"])), ("a4", "portrait", 210))
    # Writer keeps these in 1/100 mm through twips, so A4 answers 210.01.
    check("and never exactly what was asked for",
          layout["width_mm"] == 210.0, False)
    check("with margins and one column",
          (layout["margins_mm"]["left"], layout["columns"]), (20.0, 1))

    # Measured: IsLandscape on its own leaves the page upright, so the tool
    # swaps the size to match the word.
    turned = bridge.set_page_layout(orientation="landscape", doc=laid)
    check("turning the page turns it",
          (turned.get("orientation"), turned["width_mm"] > turned["height_mm"],
           turned.get("paper")), ("landscape", True, "a4"))
    bridge.set_page_layout(orientation="portrait", doc=laid)
    check("another paper",
          bridge.set_page_layout(paper="letter", doc=laid).get("paper"),
          "letter")
    margins = bridge.set_page_layout(margins_mm={"left": 30, "right": 15},
                                     doc=laid)
    check("margins, to within the rounding",
          (round(margins["margins_mm"]["left"]),
           round(margins["margins_mm"]["right"]),
           round(margins["was"]["margins_mm"]["left"])), (30, 15, 20))
    check("columns on the page",
          bridge.set_page_layout(columns=2, doc=laid).get("columns"), 2)
    check("and back to one",
          bridge.set_page_layout(columns=1, doc=laid).get("columns"), 1)
    check("a paper nobody has",
          bridge.set_page_layout(paper="papyrus", doc=laid).get("code"),
          "INVALID_PARAMETER")
    bridge.set_page_layout(paper="a4", margins_mm={"left": 20, "right": 20},
                           doc=laid)

    print("\n   breaks:")
    broken = bridge.set_page_break({"paragraph": 1}, doc=laid)
    print("   ", broken)
    check("a page break goes on the paragraph after it",
          (broken.get("success"), broken.get("break_type")),
          (True, "PAGE_BEFORE"))
    switched = bridge.set_page_break({"paragraph": 2}, page_style="Landscape",
                                     page_number=1, doc=laid)
    check("and can turn the document landscape at that point",
          (switched.get("page_style"), switched.get("page_number")),
          ("Landscape", 1))
    # PageDescName refuses None outright, and "" is what clears it.
    cleared = bridge.set_page_break({"paragraph": 1}, kind="none", doc=laid)
    check("taking a break away",
          (cleared.get("break_type"), cleared.get("page_style")),
          ("NONE", None))
    check("a kind nobody has",
          bridge.set_page_break({"paragraph": 1}, kind="sideways",
                                doc=laid).get("code"), "INVALID_PARAMETER")
    check("a page style nobody has",
          bridge.set_page_break({"paragraph": 1}, page_style="Нетакой",
                                doc=laid).get("code"), "NOT_FOUND")

    print("\n   line numbers:")
    numbered = bridge.set_line_numbering(on=True, interval=5,
                                         restart_each_page=True, doc=laid)
    print("   ", {key: numbered.get(key) for key in
                  ("on", "interval", "restart_each_page", "distance_mm")})
    check("the lines are numbered",
          (numbered.get("on"), numbered.get("interval"),
           numbered.get("restart_each_page")), (True, 5, True))
    check("which the page layout reports",
          bridge.get_page_layout(doc=laid)["line_numbering"]["on"], True)
    check("and off again",
          bridge.set_line_numbering(on=False, doc=laid).get("on"), False)

    described = bridge.describe_style(name="Standard", family="page",
                                      doc=laid)
    print("   ", {key: value["value"] for key, value
                  in described["effective"].items()})
    check("describe_style asks a page style about the page",
          (described["effective"]["TextColumns"]["value"],
           described["effective"]["IsLandscape"]["value"],
           [key for key in described["effective"] if key.startswith("Char")]),
          (1, False, []))
    laid.setModified(False)
    laid.close(True)

    print("\n--- headers and footers, which belong to a page style ---")
    paged = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                         0, ())
    paged_text = paged.getText()
    stylus = paged_text.createTextCursor()
    for line in ("Title of the document", "Body text here"):
        paged_text.insertString(stylus, line, False)
        paged_text.insertControlCharacter(stylus, PARAGRAPH_BREAK, False)

    empty = bridge.list_headers_footers(doc=paged)
    check("only the page style in use is listed while nothing is on",
          [one["page_style"] for one in empty["page_styles"]], ["Standard"])
    check("with nothing on it",
          (empty["page_styles"][0]["header"]["on"],
           empty["page_styles"][0]["footer"]["on"]), (False, False))
    # Measured: while a header is off, HeaderIsShared and its neighbours
    # answer None rather than a value, so nothing about them is reported.
    check("and nothing else claimed about it",
          "same_on_both_pages" in empty["page_styles"][0]["header"], False)

    title = bridge.set_header_footer("header", "GraphQL: руководство",
                                     doc=paged)
    print("   ", {key: title.get(key) for key in
                  ("success", "page_style", "which", "text")})
    check("a running title goes on the style the caret is on",
          (title.get("success"), title.get("page_style"), title.get("text")),
          (True, "Standard", "GraphQL: руководство"))
    numbered = bridge.set_header_footer("footer", "Страница {page} из {pages}",
                                        doc=paged)
    print("   ", {key: numbered.get(key) for key in ("text", "fields")})
    check("and the placeholders in a footer become real fields",
          numbered.get("fields"), ["page", "pages"])
    check("showing a number", numbered.get("text"), "Страница 1 из 1")

    side = bridge.set_header_footer("header", "Чётные", which="left",
                                    doc=paged)
    bridge.set_header_footer("header", "Нечётные", which="right", doc=paged)
    check("asking for one side is what makes the sides differ",
          side.get("same_on_both_pages"), False)
    first = bridge.set_header_footer("header", "Титул", which="first",
                                     doc=paged)
    check("and a first page can have one of its own",
          first.get("same_on_the_first_page"), False)
    header = [one for one in bridge.list_headers_footers(doc=paged)
              ["page_styles"] if one["page_style"] == "Standard"][0]["header"]
    print("   ", header)
    check("all three are reported, each with its own words",
          (header["text"].get("left"), header["text"].get("right"),
           header["text"].get("first")),
          ("Чётные", "Нечётные", "Титул"))

    other = bridge.set_header_footer("header", "Первая страница",
                                     page_style="First Page", doc=paged)
    check("a page style can be named outright",
          (other.get("success"), other.get("page_style")),
          (True, "First Page"))
    check("a page style nobody has",
          bridge.set_header_footer("header", "x", page_style="Нетакой",
                                   doc=paged).get("code"), "NOT_FOUND")
    check("a part nobody has",
          bridge.set_header_footer("marginalia", "x", doc=paged).get("code"),
          "INVALID_PARAMETER")

    # Measured: Writer keeps nothing of a header it is told to switch off —
    # off and on again leaves it empty on both sides.
    dropped = bridge.remove_header_footer("header", doc=paged)
    print("   ", dropped)
    check("removing one hands back what it was saying",
          (dropped.get("success"), dropped["was_saying"].get("right")),
          (True, "Нечётные"))
    standard = [one for one in bridge.list_headers_footers(doc=paged)
                ["page_styles"] if one["page_style"] == "Standard"][0]
    check("the header is off and the footer beside it untouched",
          (standard["header"]["on"], standard["footer"]["on"]), (False, True))
    check("and writing it again starts from nothing",
          bridge.set_header_footer("header", "Заново",
                                   doc=paged).get("text"), "Заново")
    check("removing what is not there",
          bridge.remove_header_footer("header", page_style="Index",
                                      doc=paged).get("code"), "NOT_FOUND")
    paged.setModified(False)
    paged.close(True)

    print("\n--- sections, and the protection UNO does not enforce ---")
    regioned = desktop.loadComponentFromURL("private:factory/swriter",
                                            "_blank", 0, ())
    region_text = regioned.getText()
    quill = region_text.createTextCursor()
    for line in ("First paragraph", "Inside one", "Inside two",
                 "After the section", "Last line"):
        region_text.insertString(quill, line, False)
        region_text.insertControlCharacter(quill, PARAGRAPH_BREAK, False)
    counted = bridge.read_paragraphs(start=0, count=1,
                                     doc=regioned)["total_paragraphs"]

    made = bridge.create_section({"paragraph": 1, "through": 2}, "Правила",
                                 doc=regioned)
    print("   ", {key: made.get(key) for key in
                  ("success", "name", "address", "columns", "text")})
    check("a section covers the paragraphs it was given",
          (made.get("success"), (made.get("text") or "").replace("\r\n", "\n")),
          (True, "Inside one\nInside two"))
    check("and moves nothing: the numbering is what it was",
          bridge.read_paragraphs(start=0, count=1,
                                 doc=regioned)["total_paragraphs"], counted)
    check("a section with no columns of its own counts as one",
          made.get("columns"), 1)
    check("a name that is taken",
          bridge.create_section({"paragraph": 0}, "Правила",
                                doc=regioned).get("code"),
          "INVALID_PARAMETER")

    print("\n   protection stops the reader's keyboard and nothing else:")
    bridge.update_section("Правила", protected=True, doc=regioned)
    refused = bridge.replace_range({"paragraph": 1}, "ПЕРЕПИСАНО",
                                   flatten=True, doc=regioned)
    print("   ", refused.get("error"))
    check("so the refusal is ours",
          (refused.get("success"), refused.get("code"),
           refused.get("section")), (False, "READ_ONLY", "Правила"))
    check("and the text is untouched",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=regioned)["paragraphs"][0]["text"],
          "Inside one")
    check("runs are refused the same way",
          bridge.replace_runs({"paragraph": 1}, [{"text": "x"}],
                              doc=regioned).get("code"), "READ_ONLY")
    check("text outside the section is not refused",
          bridge.replace_range({"paragraph": 0}, "Первый", flatten=True,
                               doc=regioned).get("success"), True)
    check("and saying you mean it writes anyway",
          bridge.replace_range({"paragraph": 1}, "ВСЁ РАВНО", flatten=True,
                               allow_protected=True,
                               doc=regioned).get("success"), True)
    bridge.update_section("Правила", protected=False, doc=regioned)
    check("unprotected, the tools work again",
          bridge.replace_range({"paragraph": 1}, "Inside one", flatten=True,
                               doc=regioned).get("success"), True)

    print("\n   hiding, columns, nesting:")
    bridge.update_section("Правила", visible=False, doc=regioned)
    check("a hidden section keeps its paragraphs, numbered and readable",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=regioned)["paragraphs"][0]["text"],
          "Inside one")
    bridge.update_section("Правила", visible=True, doc=regioned)
    check("columns can be set",
          bridge.update_section("Правила", columns=2,
                                doc=regioned).get("columns"), 2)
    renamed = bridge.update_section("Правила", new_name="Раздел",
                                    doc=regioned)
    check("and a section renamed in place",
          (renamed.get("name"), renamed["was"]["name"]), ("Раздел", "Правила"))
    bridge.create_section({"paragraph": 2}, "Вложенный", doc=regioned)
    listed = bridge.list_sections(doc=regioned)
    print("   ", [(one["name"], one["inside"], one["holds"])
                  for one in listed["sections"]])
    check("sections nest, and say which way round",
          [one["inside"] for one in listed["sections"]], [None, "Раздел"])
    # A section covers whole paragraphs, so asking about one is a question
    # about overlap rather than about where a section begins.
    check("asking which sections cover a paragraph",
          bridge.list_sections({"paragraph": 2}, doc=regioned)["count"], 2)

    removed = bridge.delete_section("Вложенный", doc=regioned)
    check("removing a section leaves its paragraphs",
          (removed.get("success"), removed.get("kept_text")),
          (True, "Inside two"))
    check("with the numbering still what it was",
          bridge.read_paragraphs(start=0, count=1,
                                 doc=regioned)["total_paragraphs"], counted)
    check("a section nobody has",
          bridge.delete_section("Нетакой", doc=regioned).get("code"),
          "NOT_FOUND")
    regioned.setModified(False)
    regioned.close(True)

    print("\n--- footnotes and endnotes ---")
    noted = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                         0, ())
    page = noted.getText()
    nib = page.createTextCursor()
    for line in ("A query is the entry point", "Second paragraph here",
                 "Third one"):
        page.insertString(nib, line, False)
        page.insertControlCharacter(nib, PARAGRAPH_BREAK, False)

    footnote = bridge.add_note({"paragraph": 0, "offset": 2, "length": 5},
                               "The entry point of a schema.", doc=noted)
    print("   ", {key: footnote.get(key) for key in
                  ("success", "kind", "mark", "address")})
    # The mark is a *character* of the paragraph, not an empty marker like a
    # comment's: "A query1 is the entry point".
    check("a footnote goes in after the words it is about",
          (footnote.get("success"), footnote.get("mark"),
           footnote.get("address")),
          (True, "1", {"paragraph": 0, "offset": 7, "length": 1}))
    check("and the paragraph gains only that one character",
          bridge.read_paragraphs(start=0, count=1,
                                 doc=noted)["paragraphs"][0]["text"],
          "A query1 is the entry point")
    endnote = bridge.add_note({"paragraph": 1, "offset": 0, "length": 6},
                              "An endnote's text.", kind="endnote", label="*",
                              doc=noted)
    check("an endnote can wear a mark of its own",
          (endnote.get("kind"), endnote.get("mark")), ("endnote", "*"))

    listed = bridge.list_notes(doc=noted)
    print("   ", [(one["kind"], one["mark"], one["text"]) for one in
                  listed["notes"]])
    check("both are listed, in the order they sit in",
          [one["kind"] for one in listed["notes"]], ["footnote", "endnote"])
    check("counted by kind", (listed["footnotes"], listed["endnotes"]), (1, 1))
    check("with what each one says",
          listed["notes"][0]["text"], "The entry point of a schema.")
    check("scoped to a paragraph",
          bridge.list_notes({"paragraph": 1}, doc=noted)["count"], 1)
    check("a kind nobody knows",
          bridge.list_notes(kind="marginalia", doc=noted).get("code"),
          "INVALID_PARAMETER")

    runs = bridge.read_runs({"paragraph": 0}, doc=noted)
    marked = [run for run in runs["runs"] if run.get("note")]
    check("read_runs says which run is a mark, and what it carries",
          (len(marked), marked[0]["note"]["kind"] if marked else None),
          (1, "footnote"))
    refused = bridge.replace_range({"paragraph": 0}, "ПЕРЕПИСАНО", doc=noted)
    print("   ", refused.get("error"))
    check("a flat rewrite is refused rather than silently destructive",
          (refused.get("success"), refused.get("code")),
          (False, "WOULD_LOSE_FORMATTING"))
    check("and names what would go",
          "footnote or endnote mark" in (refused.get("error") or ""), True)
    over_the_mark = [{"text": "X" if run.get("note") else run["text"]}
                     for run in runs["runs"]]
    check("replace_runs refuses to rewrite the run the mark sits in",
          bridge.replace_runs({"paragraph": 0}, over_the_mark,
                              doc=noted).get("code"),
          "WOULD_LOSE_FORMATTING")

    around = []
    for run in runs["runs"]:
        if run.get("note"):
            around.append({"text": run["text"]})
        elif run["text"] == "A query":
            around.append({"text": "Запрос"})
        else:
            around.append({"text": " — точка входа"})
    written = bridge.replace_runs({"paragraph": 0}, around, doc=noted)
    check("but the text around it can be translated",
          (written.get("success"), written.get("runs_kept")), (True, 1))
    check("the note is still there", bridge.list_notes(doc=noted)["footnotes"],
          1)
    # An address after a mark has to count the mark's character, or a rewrite
    # around it leaves a stray letter behind — measured, and fixed here.
    check("with the paragraph reading as it should",
          bridge.read_paragraphs(start=0, count=1,
                                 doc=noted)["paragraphs"][0]["text"],
          "Запрос1 — точка входа")

    where = bridge.list_notes(kind="footnote", doc=noted)["notes"][0]["address"]
    changed = bridge.update_note(where, text="Точка входа схемы.", doc=noted)
    check("what a note says can be changed",
          (changed.get("success"), changed.get("text")),
          (True, "Точка входа схемы."))
    check("leaving the sentence alone",
          bridge.read_paragraphs(start=0, count=1,
                                 doc=noted)["paragraphs"][0]["text"],
          "Запрос1 — точка входа")
    check("saying nothing to change",
          bridge.update_note(where, doc=noted).get("code"),
          "INVALID_PARAMETER")
    check("an address with no note in it",
          bridge.update_note({"paragraph": 2}, text="x",
                             doc=noted).get("code"), "NOT_FOUND")
    removed = bridge.delete_note(where, doc=noted)
    check("removing it takes the mark out and hands back what it said",
          (removed.get("success"), removed.get("was_saying")),
          (True, "Точка входа схемы."))
    check("leaving the words",
          bridge.read_paragraphs(start=0, count=1,
                                 doc=noted)["paragraphs"][0]["text"],
          "Запрос — точка входа")

    cells = noted.createInstance("com.sun.star.text.TextTable")
    cells.initialize(2, 2)
    cells.setName("Сетка")
    page.insertTextContent(bridge._resolve_address(
        noted, {"paragraph": 2}).getStart(), cells, False)
    cells.getCellByName("A1").setString("cell text")
    in_cell = bridge.add_note({"table": "Сетка", "cell": "A1", "offset": 0,
                               "length": 4}, "From a cell.", doc=noted)
    check("a footnote works inside a table cell, addressed to it",
          (in_cell.get("success"), (in_cell.get("address") or {}).get("cell")),
          (True, "A1"))
    noted.setModified(False)
    noted.close(True)

    print("\n--- the tables a document writes about itself ---")
    # On a document of its own again: an index's entries are body paragraphs,
    # so it moves every address in the document it is put into.
    indexed = desktop.loadComponentFromURL("private:factory/swriter",
                                           "_blank", 0, ())
    written = indexed.getText()
    pen = written.createTextCursor()
    for style, line in (("Heading 1", "Introduction"),
                        ("Standard", "Intro body with GraphQL"),
                        ("Heading 2", "Scalar types"),
                        ("Standard", "Scalars body"),
                        ("Heading 1", "Conclusion"),
                        ("Standard", "The end")):
        pen.ParaStyleName = style
        written.insertString(pen, line, False)
        written.insertControlCharacter(pen, PARAGRAPH_BREAK, False)

    contents = bridge.insert_index({"paragraph": 0}, title="Содержание",
                                   doc=indexed)
    print("   ", {key: contents.get(key) for key in
                  ("success", "name", "kind", "entries", "paragraphs_added")})
    check("a table of contents goes in and writes itself",
          (contents.get("success"), contents.get("entries")), (True, 3))
    check("naming itself, which is how it is named back",
          contents.get("name"), "Table of Contents1")
    # UNO's own defaults are CreateFromOutline False and CreateFromMarks
    # True, which lists nothing at all in a document nobody has marked up.
    check("built from the headings rather than from marks",
          (contents.get("from_outline"), contents.get("from_marks")),
          (True, False))
    check("and it cost the document four paragraphs",
          contents.get("paragraphs_added"), 4)
    entry = bridge.read_paragraphs(start=0, count=4, doc=indexed)["paragraphs"]
    check("the entries are ordinary paragraphs, in the contents styles",
          (entry[1]["text"], entry[1]["style"]), ("Introduction\t1",
                                                  "Contents 1"))

    print("\n   a heading rewritten, and the index updated:")
    heading = bridge.find_text("Introduction", doc=indexed)["hits"][-1]
    bridge.replace_range({"paragraph": heading["address"]["paragraph"]},
                         "Введение", flatten=True, doc=indexed)
    stale = bridge.read_paragraphs(start=1, count=1,
                                   doc=indexed)["paragraphs"][0]["text"]
    updated = bridge.update_indexes(all=True, doc=indexed)
    check("the entry was stale until it was updated", stale, "Introduction\t1")
    check("and follows the heading afterwards",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=indexed)["paragraphs"][0]["text"],
          "Введение\t1")
    check("with the result saying nothing moved this time",
          updated.get("paragraphs_moved"), 0)

    print("\n   an alphabetical index, from the marks:")
    word = bridge.find_text("GraphQL", doc=indexed)["hits"][0]
    marked = bridge.add_index_mark(word["address"], "GraphQL", doc=indexed)
    check("a mark covers its word and leaves it",
          (marked.get("success"), marked.get("marked")), (True, "GraphQL"))
    total = bridge.read_paragraphs(start=0, count=1,
                                   doc=indexed)["total_paragraphs"]
    alphabetical = bridge.insert_index({"paragraph": total - 1},
                                       kind="alphabetical", title="Указатель",
                                       doc=indexed)
    check("an alphabetical index lists what was marked",
          (alphabetical.get("success"), alphabetical.get("kind")),
          (True, "alphabetical"))
    check("under its key",
          bridge.read_paragraphs(start=alphabetical["address"]["paragraph"],
                                 count=3,
                                 doc=indexed)["paragraphs"][1]["text"]
          .startswith("GraphQL"), True)

    listed = bridge.list_indexes(doc=indexed)
    print("   ", [(one["name"], one["kind"], one["entries"])
                  for one in listed["indexes"]])
    check("both are listed, in the order they stand in",
          [one["kind"] for one in listed["indexes"]],
          ["contents", "alphabetical"])
    check("and Writer protects them from being typed over",
          all(one["protected"] for one in listed["indexes"]), True)
    check("one can be updated by name",
          bridge.update_indexes(name="Table of Contents1",
                                doc=indexed).get("updated"),
          ["Table of Contents1"])
    check("naming none, or two ways at once",
          (bridge.update_indexes(doc=indexed).get("code"),
           bridge.update_indexes(name="Table of Contents1", all=True,
                                 doc=indexed).get("code")),
          ("INVALID_PARAMETER", "INVALID_PARAMETER"))
    check("an index nobody has",
          bridge.update_indexes(name="Нетакой", doc=indexed).get("code"),
          "NOT_FOUND")
    check("a kind nobody knows",
          bridge.insert_index({"paragraph": 0}, kind="gossip",
                              doc=indexed).get("code"), "INVALID_PARAMETER")

    removed = bridge.delete_index("Table of Contents1", doc=indexed)
    check("removing an index takes the paragraphs it wrote",
          (removed.get("success"), removed.get("paragraphs_removed")),
          (True, 4))
    check("leaving the document as it was",
          bridge.read_paragraphs(start=0, count=1,
                                 doc=indexed)["paragraphs"][0]["text"],
          "Введение")
    indexed.setModified(False)
    indexed.close(True)

    print("\n--- changing a table's shape, and the order of its rows ---")
    shaped = doc.createInstance("com.sun.star.text.TextTable")
    shaped.initialize(4, 3)
    shaped.setName("Форма")
    tail = body.createTextCursorByRange(body.getEnd())
    body.insertTextContent(tail, shaped, False)
    for row, values in enumerate((["Name", "Size", "Note"], ["b", "2", "x"],
                                  ["a", "10", "y"], ["c", "1", "z"])):
        for column, value in zip("ABC", values):
            shaped.getCellByName(f"{column}{row + 1}").setString(value)
    shaped.HeaderRowCount = 1

    added = bridge.insert_table_rows("Форма", at=1, count=2, doc=doc)
    print("   ", {key: added.get(key) for key in ("success", "rows")})
    check("two rows added where asked", (added.get("success"), added.get("rows")),
          (True, 6))
    check("and the text moved down, not away",
          bridge.read_table("Форма", cell="A4", doc=doc)["text"], "b")
    taken = bridge.delete_table_rows("Форма", at=1, count=2, doc=doc)
    check("and away again", (taken.get("success"), taken.get("rows")), (True, 4))
    check("saying what went with them", taken.get("removed"), [["", "", ""],
                                                               ["", "", ""]])

    column_added = bridge.insert_table_columns("Форма", at=3, count=1, doc=doc)
    check("a column at the end", column_added.get("columns"), 4)
    column_gone = bridge.delete_table_columns("Форма", at=3, count=1, doc=doc)
    check("and away", column_gone.get("columns"), 3)
    check("a table cannot lose all its rows",
          bridge.delete_table_rows("Форма", at=0, count=4,
                                   doc=doc).get("code"), "INVALID_PARAMETER")
    check("nor be asked for a row it has not got",
          bridge.delete_table_rows("Форма", at=99, doc=doc).get("code"),
          "INVALID_PARAMETER")

    print("\n   sorting:")
    sorted_out = bridge.sort_table(column="B", numeric=True, name="Форма",
                                   doc=doc)
    print("   ", {key: sorted_out.get(key) for key in
                  ("success", "sorted_by", "rows_moved", "header_rows_kept")})
    check("sorted by the column asked for",
          [bridge.read_table("Форма", cell=f"A{row}", doc=doc)["text"]
           for row in (1, 2, 3, 4)],
          ["Name", "c", "b", "a"])
    check("the heading stayed where it was",
          bridge.read_table("Форма", cell="A1", doc=doc)["text"], "Name")
    backwards = bridge.sort_table(column=1, descending=True, name="Форма",
                                  doc=doc)
    check("and by text, backwards",
          [bridge.read_table("Форма", cell=f"A{row}", doc=doc)["text"]
           for row in (2, 3, 4)], ["c", "b", "a"])
    check("a column this table has not got is refused",
          bridge.sort_table(column="Z", name="Форма", doc=doc).get("code"),
          "INVALID_PARAMETER")

    print("\n   merging and splitting:")
    merged = bridge.merge_table_cells("A2:B2", name="Форма", doc=doc)
    print("   ", {key: merged.get(key) for key in
                  ("success", "into", "text", "cells_left")})
    check("two cells became one", merged.get("success"), True)
    check("keeping both texts", merged.get("text", "").replace("\r\n", "\n"), "c\n1")
    check("a table with merged cells will not be sorted",
          bridge.sort_table(column=1, name="Форма", doc=doc).get("code"),
          "UNSUPPORTED")
    split = bridge.split_table_cells("A2", into=2, direction="columns",
                                     name="Форма", doc=doc)
    check("and split back", split.get("success"), True)
    check("one cell is not a merge",
          bridge.merge_table_cells("A3", name="Форма", doc=doc).get("code"),
          "INVALID_PARAMETER")

    bridge.delete_table("Форма", doc=doc)

    print("\n--- reading the recorded changes, and settling them ---")
    marker = body.createTextCursorByRange(body.getEnd())
    for line in ("REVIEW-ONE stays as it is", "REVIEW-TWO loses a word"):
        body.insertControlCharacter(marker, PARAGRAPH_BREAK, False)
        body.insertString(marker, line, False)
    total = bridge.read_paragraphs(start=0, count=1, doc=doc)["total_paragraphs"]
    first, second = total - 2, total - 1

    was_recording = doc.RecordChanges
    doc.RecordChanges = True
    bridge.replace_range({"paragraph": first, "offset": 0, "length": 10},
                         "REVIEW-ONE ", track_changes=True, doc=doc)
    bridge.replace_range({"paragraph": second, "offset": 0, "length": 10},
                         "", track_changes=True, doc=doc)
    doc.RecordChanges = False

    listed = bridge.list_tracked_changes({"paragraph": first, "through": second},
                                         doc=doc)
    print("   ", [(one["kind"], one["text"][:20], one["address"])
                  for one in listed["changes"]])
    check("both changes are reported", listed["count"] >= 2, True)
    check("with the kinds Writer recorded",
          {one["kind"] for one in listed["changes"]} <= {"insert", "delete",
                                                         "format"}, True)
    check("each one addressed",
          all(one["address"] is not None for one in listed["changes"]), True)
    check("each one carrying the text it covers",
          all(one["text"] for one in listed["changes"]), True)
    check("and a date", all(one["date"] for one in listed["changes"]), True)
    check("recording is reported as it stands now",
          listed["recording"], False)

    check("settling without saying which is refused",
          bridge.accept_tracked_changes(doc=doc).get("code"),
          "INVALID_PARAMETER")
    check("and naming two ways at once",
          bridge.accept_tracked_changes(all=True, author="anyone",
                                        doc=doc).get("code"),
          "INVALID_PARAMETER")
    check("a change that is not there",
          bridge.reject_tracked_changes(change_id="nosuch",
                                        doc=doc).get("code"), "NOT_FOUND")

    one = listed["changes"][0]
    accepted = bridge.accept_tracked_changes(change_id=one["id"], doc=doc)
    print("   ", {key: accepted.get(key) for key in
                  ("success", "settled", "decision", "left")})
    check("one change accepted", (accepted.get("success"),
                                  accepted.get("settled")), (True, 1))
    check("and it is gone from the list",
          one["id"] not in [other["id"] for other in
                            bridge.list_tracked_changes(doc=doc)["changes"]],
          True)

    rest = bridge.reject_tracked_changes(
        address={"paragraph": first, "through": second}, doc=doc)
    print("   ", {key: rest.get(key) for key in ("success", "settled", "left")})
    check("the rest rejected by place", rest.get("success"), True)
    check("nothing recorded is left in those paragraphs",
          bridge.list_tracked_changes({"paragraph": first, "through": second},
                                      doc=doc)["count"], 0)
    check("the accepted insertion stayed in the text",
          "REVIEW-ONE " in bridge.read_paragraphs(
              start=first, count=1, doc=doc)["paragraphs"][0]["text"], True)
    check("and the rejected deletion came back",
          bridge.read_paragraphs(start=second, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "REVIEW-TWO loses a word")

    print("\n   a rewrite over text a recorded change marks:")
    doc.RecordChanges = True
    bridge.replace_range({"paragraph": second, "offset": 0, "length": 6},
                         "REVIEWED", track_changes=True, doc=doc)
    doc.RecordChanges = False
    marked = bridge.read_runs({"paragraph": second}, doc=doc)
    check("the runs say which of them a change covers",
          any(run.get("changes") for run in marked["runs"]), True)
    carried = sorted({one["kind"] for run in marked["runs"]
                      for one in run.get("changes") or []})
    check("naming the kinds Writer recorded",
          set(carried) <= {"insert", "delete", "format"} and bool(carried),
          True)
    refused = bridge.replace_runs(
        {"paragraph": second},
        [dict(run, text=run["text"].upper()) for run in marked["runs"]],
        doc=doc)
    print("   ", refused.get("error"))
    check("a rewrite over them is refused, not written",
          (refused.get("success"), refused.get("code")),
          (False, "WOULD_LOSE_FORMATTING"))
    check("and it names the way through",
          "accept_tracked_changes" in (refused.get("error") or ""), True)
    seen = len({one["id"] for run in marked["runs"]
                for one in run.get("changes") or []})
    flat = bridge.replace_range({"paragraph": second}, "ПЕРЕПИСАНО ПОВЕРХ",
                                flatten=True, doc=doc)
    check("flatten writes over them and says how many went",
          (flat.get("success"), flat.get("changes_dropped")), (True, seen))
    check("and they are gone from the list",
          bridge.list_tracked_changes({"paragraph": second}, doc=doc)["count"],
          0)

    doc.RecordChanges = was_recording
    for _ in range(2):
        last = bridge._paragraph_at(body, bridge.read_paragraphs(
            start=0, count=1, doc=doc)["total_paragraphs"] - 1)
        body.removeTextContent(last)

    print("\n--- a review conversation: a comment and the replies on it ---")
    marker = body.createTextCursorByRange(body.getEnd())
    body.insertControlCharacter(marker, PARAGRAPH_BREAK, False)
    body.insertString(marker, "query is the entry point", False)
    talk = bridge.read_paragraphs(start=0, count=1,
                                  doc=doc)["total_paragraphs"] - 1

    parent = bridge.add_comment({"paragraph": talk, "offset": 0, "length": 5},
                                "Is this the right term?", author="Reviewer",
                                doc=doc)
    reply = bridge.add_comment(text="Yes — the spec uses it.", author="Claude",
                               reply_to=parent["id"], doc=doc)
    print("   ", {key: reply.get(key) for key in
                  ("success", "reply_to", "anchor_text")})
    check("a reply is anchored on its parent's own text",
          (reply.get("success"), reply.get("anchor_text")), (True, "query"))
    check("a reply takes no address of its own",
          bridge.add_comment({"paragraph": talk}, "no",
                             reply_to=parent["id"], doc=doc).get("success"),
          False)
    check("and a parent that is not there is refused",
          bridge.add_comment(text="hello?", reply_to="__Annotation__nobody",
                             doc=doc).get("code"), "NOT_FOUND")

    listed = bridge.list_comments({"paragraph": talk}, doc=doc)
    check("the thread is reported as one conversation",
          (listed["threads"], listed["replies"]), (1, 1))
    top = [one for one in listed["comments"] if not one["reply_to"]][0]
    check("with the replies hanging off the parent",
          top["replies"], [reply["id"]])

    deeper = bridge.add_comment(text="Agreed.", author="Reviewer",
                                reply_to=reply["id"], doc=doc)
    check("a reply to a reply is a chain",
          bridge.add_comment(text="one more", reply_to=deeper["id"],
                             doc=doc).get("success"), True)

    refused = bridge.delete_comment(parent["id"], doc=doc)
    print("   ", refused.get("error"))
    check("deleting the parent alone is refused rather than orphaning",
          (refused.get("success"), refused.get("code")),
          (False, "INVALID_PARAMETER"))

    print("\n   the thread through a rewrite of the text it sits on:")
    runs = bridge.read_runs({"paragraph": talk}, doc=doc)
    carried = [len(run.get("comments") or []) for run in runs["runs"]]
    check("the runs carry all four notes", max(carried), 4)
    translated = [dict(run, text="запрос" if run["text"] == "query"
                       else run["text"]) for run in runs["runs"]]
    written = bridge.replace_runs({"paragraph": talk}, translated, doc=doc)
    print("   ", {key: written.get(key) for key in
                  ("success", "comments_kept", "comments_written")})
    check("the rewrite went through", written.get("success"), True)
    after = bridge.list_comments({"paragraph": talk}, doc=doc)
    check("the conversation is still one thread of four",
          (after["count"], after["threads"], after["replies"]), (4, 1, 3))
    root = [one for one in after["comments"] if not one["reply_to"]][0]
    check("and the parent is the one it was",
          root["content"], "Is this the right term?")
    check("every reply still names a comment that is there",
          all(one["reply_to"] in {c["id"] for c in after["comments"]}
              for one in after["comments"] if one["reply_to"]), True)

    print("\n   making the parent again, in another language:")
    remade = bridge.update_comment(root["id"], language="ru-RU", doc=doc)
    check("the parent was made again", remade.get("recreated"), True)
    check("and its replies were re-pointed at the new id",
          len(remade.get("replies_repointed") or []), 1)
    joined = bridge.list_comments({"paragraph": talk}, doc=doc)
    check("so the thread survived the new id",
          (joined["threads"], joined["replies"]), (1, 3))

    print("\n   many at once:")
    root_id = [one for one in joined["comments"] if not one["reply_to"]][0]["id"]
    marked = bridge.resolve_comments(comment_id=root_id, doc=doc)
    print("   ", {key: marked.get(key) for key in
                  ("success", "marked", "replies_followed")})
    check("resolving a thread takes its replies with it",
          marked.get("replies_followed") >= 1, True)
    check("and the whole thread reads as resolved",
          bridge.list_comments({"paragraph": talk}, doc=doc)["unresolved"], 0)
    check("reopening by author",
          bridge.resolve_comments(author="Reviewer", resolved=False,
                                  doc=doc).get("success"), True)
    check("some are open again",
          bridge.list_comments({"paragraph": talk}, doc=doc)["unresolved"] > 0,
          True)
    check("listing by author",
          {one["author"] for one in bridge.list_comments(
              {"paragraph": talk}, author="Claude", doc=doc)["comments"]},
          {"Claude"})
    check("and settling without saying which is refused",
          bridge.resolve_comments(doc=doc).get("code"), "INVALID_PARAMETER")

    whole = bridge.delete_comment(
        comment_id=root_id, with_replies=True, doc=doc)
    check("and the whole thread can be taken at once",
          (whole.get("success"), len(whole.get("replies_deleted") or [])),
          (True, 3))
    check("leaving no comments on that paragraph",
          bridge.list_comments({"paragraph": talk}, doc=doc)["count"], 0)
    check("and the text it was about",
          bridge.read_paragraphs(start=talk, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "запрос is the entry point")
    body.removeTextContent(bridge._paragraph_at(body, talk))

    print("\n--- formulas, which are objects and not text ---")
    sentence = "Объем первой части равен  от всего объема."
    after_equals = len("Объем первой части равен ")
    writing = body.createTextCursorByRange(body.getEnd())
    body.insertControlCharacter(writing, PARAGRAPH_BREAK, False)
    body.insertString(writing, sentence, False)
    here = bridge.read_paragraphs(start=0, count=1,
                                  doc=doc)["total_paragraphs"] - 1
    made = bridge.add_formula({"paragraph": here, "offset": after_equals,
                               "length": 0}, FIFTH, name="доля", doc=doc)
    print("   ", {key: made.get(key) for key in
                  ("success", "name", "formula", "address", "inline",
                   "width_mm", "height_mm")})
    check("a formula goes in", made.get("success"), True)
    read_here = bridge.read_paragraphs(start=here, count=1,
                                       doc=doc)["paragraphs"][0]
    check("and adds no character to the paragraph", read_here["text"], sentence)
    check("the paragraph says what its string leaves out",
          read_here.get("text_with_formulas"),
          f"Объем первой части равен ⟦formula: {FIFTH}⟧ от всего объема.")
    # Writer leaves a new object at 35.28 x 4.71 mm whatever is written in it,
    # which is what made inserted formulas look distorted.
    check("it is sized from the Math document, not left as Writer made it",
          (made.get("width_mm"), made.get("height_mm")) != (35.28, 4.71), True)

    # Answering about one paragraph must not place every formula in the
    # document: that addresses every anchor, which walks the body.
    swept = []
    placing = bridge._formula_places

    def counted(*arguments, **named):
        swept.append(True)
        return placing(*arguments, **named)

    bridge._formula_places = counted
    try:
        runs = bridge.read_runs({"paragraph": here}, doc=doc)
        elsewhere = bridge.read_runs({"paragraph": here - 1}, doc=doc)
    finally:
        bridge._formula_places = placing
    check("the runs of a paragraph say which formula stands among them",
          runs.get("formulas"),
          [{"name": "доля", "formula": FIFTH, "paragraph": here,
            "offset": after_equals}])
    check("a paragraph without one says nothing of formulas",
          "formulas" in elsewhere, False)
    check("and neither reading placed every formula in the document",
          swept, [])

    listed = bridge.list_formulas(doc=doc)
    check("listed with its place",
          [(one["name"], one["address"]["paragraph"], one["address"]["offset"])
           for one in listed["formulas"]], [("доля", here, after_equals)])
    check("and with the words on either side of it",
          (listed["formulas"][0].get("text_before", "")[-6:],
           listed["formulas"][0].get("text_after", "")[:3]),
          ("равен ", " от"))
    changed = bridge.set_formula("доля", "{ frac { 1 } { 3 } }", doc=doc)
    check("changed, saying what it was",
          (changed.get("success"), changed.get("was")), (True, FIFTH))
    gone = bridge.delete_formula("доля", doc=doc)
    check("deleted, leaving the text",
          (gone.get("success"),
           bridge.read_paragraphs(start=here, count=1,
                                  doc=doc)["paragraphs"][0]["text"]),
          (True, sentence))
    check("and the document has none left",
          bridge.list_formulas(doc=doc)["count"], 0)
    body.removeTextContent(bridge._paragraph_at(body, here))

    print("\n--- a plan carried out in one call, and taken back in one step ---")
    from mcp_server import LibreOfficeMCPServer
    server = LibreOfficeMCPServer.__new__(LibreOfficeMCPServer)
    server.uno_bridge = bridge
    server.tools = {}
    server._register_tools()
    check("the server registers its tools", len(server.tools) >= 45, True)

    marker = body.createTextCursorByRange(body.getEnd())
    for line in ("BATCH-ONE", "BATCH-TWO", "BATCH-THREE"):
        body.insertControlCharacter(marker, PARAGRAPH_BREAK, False)
        body.insertString(marker, line, False)
    total = bridge.read_paragraphs(start=0, count=1, doc=doc)["total_paragraphs"]
    first = total - 3

    batched = server.batch_live(
        [{"tool": "replace_range_live",
          "parameters": {"address": {"paragraph": first + offset},
                         "text": f"BATCHED-{offset}"}}
         for offset in (0, 1, 2)],
        undo_title="MCP: three at once")
    print("   ", {key: batched[key] for key in
                  ("success", "steps", "done", "failed", "undo_title")})
    check("every step ran", (batched["done"], batched["failed"]), (3, 0))
    check("and the text is theirs",
          [entry["text"] for entry in bridge.read_paragraphs(
              start=first, count=3, doc=doc)["paragraphs"]],
          ["BATCHED-0", "BATCHED-1", "BATCHED-2"])
    titles = list(doc.UndoManager.getAllUndoActionTitles())
    # Not by counting: Writer's undo stack has a limit, and on a full one a
    # new entry pushes the oldest out without the count moving.
    check("three edits left one undo entry, named after the batch",
          (titles[0], titles[1] != "MCP: three at once"),
          ("MCP: three at once", True))

    doc.UndoManager.undo()
    check("and one undo takes all three back",
          [entry["text"] for entry in bridge.read_paragraphs(
              start=first, count=3, doc=doc)["paragraphs"]],
          ["BATCH-ONE", "BATCH-TWO", "BATCH-THREE"])

    failing = server.batch_live(
        [{"tool": "replace_range_live",
          "parameters": {"address": {"paragraph": first}, "text": "HALF-DONE"}},
         {"tool": "replace_range_live",
          "parameters": {"address": {"paragraph": 99999}, "text": "nowhere"}}],
        on_error="undo")
    print("   ", failing.get("error"))
    check("a failed batch is taken back whole",
          (failing["success"], failing["undone"]), (False, True))
    check("so the document is as it was",
          bridge.read_paragraphs(start=first, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "BATCH-ONE")

    check("a batch of a tool that does not exist is refused before it runs",
          server.batch_live([{"tool": "translate_live"}]).get("success"), False)
    check("and a batch inside a batch",
          server.batch_live([{"tool": "batch_live"}]).get("success"), False)

    top_before_reading = doc.UndoManager.getAllUndoActionTitles()[0]
    read_back = server.batch_live([{"tool": "read_paragraphs_live",
                                    "parameters": {"start": first, "count": 1}}])
    check("a batch that only reads leaves no undo entry",
          doc.UndoManager.getAllUndoActionTitles()[0], top_before_reading)
    check("and carries the result of every step",
          read_back["results"][0]["result"]["paragraphs"][0]["text"],
          "BATCH-ONE")

    for _ in range(3):                      # take this section's paragraphs away
        last = bridge._paragraph_at(body, bridge.read_paragraphs(
            start=0, count=1, doc=doc)["total_paragraphs"] - 1)
        body.removeTextContent(last)

    print("\n--- saving under a name, closing, renaming ---")
    import zipfile
    yard = tmp_path("mcp_live_documents")
    if os.path.isdir(yard):
        for leftover in os.listdir(yard):
            os.unlink(os.path.join(yard, leftover))
    else:
        os.makedirs(yard)

    fresh = desktop.loadComponentFromURL("private:factory/swriter", "_blank",
                                         0, ())
    fresh.getText().setString("A document to check saving")
    check("a new document lives nowhere yet", fresh.hasLocation(), False)
    check("and saving it without a name is refused",
          bridge.save_document(doc=fresh).get("success"), False)

    odt = os.path.join(yard, "guide.odt")
    saved = bridge.save_document(doc=fresh, file_path=odt)
    print("   ", saved)
    check("saved under a name", saved.get("success"), True)
    check("with Writer's own filter", saved.get("filter"), "writer8")
    check("the document lives there now", fresh.getURL(),
          file_uri(odt))
    check("and the file is real ODF",
          zipfile.ZipFile(odt).read("mimetype").decode(),
          "application/vnd.oasis.opendocument.text")

    word = os.path.join(yard, "guide.docx")
    as_word = bridge.save_document(doc=fresh, file_path=word)
    print("   ", as_word)
    check("saved as Word", as_word.get("filter"), "MS Word 2007 XML")
    check("and it really is OOXML, not ODF with a .docx name",
          "[Content_Types].xml" in zipfile.ZipFile(word).namelist(), True)
    check("the document followed the name", fresh.getURL(), file_uri(word))

    check("a format nothing here writes is refused",
          bridge.save_document(doc=fresh,
                               file_path=os.path.join(yard, "x.pages")
                               ).get("success"), False)
    check("and PDF is sent to export_document",
          "export_document" in bridge.save_document(
              doc=fresh, file_path=os.path.join(yard, "x.pdf"))["error"], True)
    check("an existing file is not written over",
          bridge.save_document(doc=fresh, file_path=odt).get("success"), False)

    print("\n   renaming:")
    renamed = bridge.rename_document("guide-v2.docx", doc=fresh)
    print("   ", renamed)
    check("renamed", renamed.get("success"), True)
    check("the document is called that now", fresh.getURL(),
          file_uri(os.path.join(yard, "guide-v2.docx")))
    check("the old file is still there, as UNO leaves it",
          os.path.exists(word), True)
    check("which the result says", renamed.get("original_kept"), True)

    removed = bridge.rename_document("guide-v3.docx", doc=fresh,
                                     delete_original=True)
    check("and it can be removed when asked", removed.get("original_kept"),
          False)
    check("the old name is gone",
          os.path.exists(os.path.join(yard, "guide-v2.docx")), False)
    check("a bare name keeps the directory and the extension",
          bridge.rename_document("Руководство", doc=fresh,
                                 delete_original=True)["renamed_to"],
          os.path.join(yard, "Руководство.docx"))

    print("\n   closing:")
    fresh.getText().setString("changed and unsaved")
    check("a document with unsaved changes is not closed",
          bridge.close_document(doc=fresh).get("success"), False)
    check("it is still open", fresh.hasLocation(), True)
    kept = bridge.close_document(doc=fresh, unsaved="save")
    print("   ", kept)
    check("closed, saving the changes", (kept.get("success"),
                                         kept.get("changes_saved")),
          (True, True))
    check("and what it saved is on disk",
          os.path.getsize(os.path.join(yard, "Руководство.docx")) > 0, True)

    throwaway = desktop.loadComponentFromURL("private:factory/swriter",
                                             "_blank", 0, ())
    throwaway.getText().setString("this need not be saved")
    check("a document that lives nowhere cannot save on the way out",
          bridge.close_document(doc=throwaway, unsaved="save").get("success"),
          False)
    let_go = bridge.close_document(doc=throwaway, unsaved="discard")
    check("but it can be let go", (let_go.get("success"),
                                   let_go.get("changes_discarded")),
          (True, True))
    for leftover in os.listdir(yard):
        os.unlink(os.path.join(yard, leftover))
    os.rmdir(yard)

    doc.setModified(False)
    doc.close(True)
    desktop.terminate()
finally:
    time.sleep(2)
    if soffice.poll() is None:
        soffice.terminate()
        try:
            soffice.wait(timeout=15)
        except subprocess.TimeoutExpired:
            soffice.kill()

print("\n" + ("FAILURES: " + ", ".join(failures) if failures
              else "ALL LIVE CHECKS PASSED"))
sys.exit(1 if failures else 0)
