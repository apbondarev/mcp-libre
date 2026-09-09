"""Check the Writer tools against a real LibreOffice.

Runs its own headless instance with a separate user profile, so a developer's
session is untouched. Must run under /usr/bin/python3, which carries the
python3-uno bindings; the repo venv has no uno module:

    /usr/bin/python3 tests/live/writer_tools_check.py

The fakes in tests/fake_writer.py encode assumptions about UNO. This checks
them. Anything unverified here is not known to work.
"""

import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "plugin", "pythonpath"))

import uno  # noqa: E402
from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK  # noqa: E402

PORT = 2010
PROFILE = "/tmp/mcp_live_check_profile"
failures = []


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


soffice = subprocess.Popen([
    "soffice", f"-env:UserInstallation=file://{PROFILE}",
    "--headless", "--norestore", "--nologo", "--nodefault",
    f"--accept=socket,host=127.0.0.1,port={PORT};urp;",
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    ctx = connect()
    desktop = ctx.ServiceManager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", ctx)
    doc = build_document(desktop)

    from uno_bridge import UNOBridge
    bridge = UNOBridge.__new__(UNOBridge)  # no local desktop wanted
    bridge.ctx = ctx
    bridge.smgr = ctx.ServiceManager

    print("--- get_outline ---")
    outline = bridge.get_outline(doc)
    print(outline)
    check("outline success", outline.get("success"), True)
    check("heading texts", [h["text"] for h in outline["headings"]],
          ["Chapter One", "Section A"])
    check("heading levels", [h["level"] for h in outline["headings"]], [1, 2])
    check("heading paragraphs", [h["paragraph"] for h in outline["headings"]], [0, 2])

    print("\n--- read_paragraphs ---")
    window = bridge.read_paragraphs(start=1, count=2, doc=doc)
    print(window)
    check("window texts", [p["text"] for p in window["paragraphs"]],
          ["Alpha beta alpha.", "Section A"])
    check("window indices", [p["paragraph"] for p in window["paragraphs"]], [1, 2])
    check("style of a heading", window["paragraphs"][1]["style"], "Heading 2")

    print("\n--- find_text ---")
    found = bridge.find_text("alpha", doc=doc)
    print(found)
    check("hit count", found.get("total_hits"), 2)
    check("first hit address", found["hits"][0]["address"],
          {"paragraph": 1, "offset": 0, "length": 5})
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

    result = bridge.replace_selection("Первый", doc=doc)
    print(result)
    check("replace succeeded", result.get("success"), True)
    check("paragraph rewritten", target.getString(), "Первый beta alpha.")
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
    tracked = bridge.replace_selection("Второй", track_changes=True, doc=doc)
    print(tracked)
    check("tracked flag", tracked.get("tracked"), True)
    check("new text present", "Второй" in target.getString(), True)
    check("original still there as a tracked deletion",
          "Alpha" in target.getString(), True)
    check("redlines recorded", doc.getRedlines().getCount() > 0, True)
    check("document's own RecordChanges restored", doc.RecordChanges, False)
    undo.undo()

    print("\n--- replace_range by address: translating a heading ---")
    outline_before = bridge.get_outline(doc)
    heading = outline_before["headings"][1]        # "Section A" at its index
    print("heading to rewrite:", heading)
    replaced = bridge.replace_range({"paragraph": heading["paragraph"]},
                                    "Раздел А", doc=doc)
    print(replaced)
    check("replace_range succeeded", replaced.get("success"), True)

    outline_after = bridge.get_outline(doc)
    check("heading text is translated",
          [h["text"] for h in outline_after["headings"]],
          ["Chapter One", "Раздел А"])
    check("heading is still a heading at the same level",
          outline_after["headings"][1]["level"], heading["level"])
    check("heading is still at the same paragraph",
          outline_after["headings"][1]["paragraph"], heading["paragraph"])
    check("paragraph count unchanged",
          outline_after["total_paragraphs"], outline_before["total_paragraphs"])

    print("\n--- replace_range on part of a paragraph ---")
    body_paragraph = 3                              # "Gamma delta."
    part = bridge.replace_range(
        {"paragraph": body_paragraph, "offset": 0, "length": 5}, "Гамма", doc=doc)
    check("partial replace succeeded", part.get("success"), True)
    check("only the addressed part changed",
          bridge.read_paragraphs(start=body_paragraph, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "Гамма delta.")

    print("\n--- a search hit's address can be rewritten straight away ---")
    hit = bridge.find_text("beta", doc=doc)["hits"][0]
    rewritten = bridge.replace_range(hit["address"], "БЕТА", doc=doc)
    check("hit rewritten", rewritten.get("success"), True)
    check("text now holds the replacement",
          "БЕТА" in bridge.read_paragraphs(
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

    refused = bridge.replace_range({"paragraph": 3}, "перевод", doc=doc)
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
    flattened = bridge.replace_range({"paragraph": 3}, "перевод",
                                     language="ru-RU", flatten=True, doc=doc)
    print(flattened)
    check("went ahead", flattened.get("success"), True)
    check("reported the runs it flattened", flattened.get("runs_flattened"),
          before["count"])
    check("one run left afterwards",
          bridge.read_runs({"paragraph": 3}, doc=doc)["count"], 1)

    print("\n--- a uniform paragraph is replaced without ceremony ---")
    plain = bridge.replace_range({"paragraph": 3}, "простой текст", doc=doc)
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
                                  "Термин – не переводится",
                                  author="Ревьюер", doc=doc)
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
    check("with its author", listed["comments"][0]["author"], "Ревьюер")
    check("with its text", listed["comments"][0]["content"],
          "Термин – не переводится")
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
          all(r["comments"][0]["content"] == "Термин – не переводится"
              for r in covered), True)
    check("the run past the anchor carries none",
          [r["comments"] for r in runs if r["text"] == " are roots"], [[]])

    print("\n--- a flat replacement is refused, and counts it once ---")
    refused = bridge.replace_range({"paragraph": 3}, "перевод", doc=doc)
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
          "Термин – не переводится")
    check("its author survived", after["comments"][0]["author"], "Ревьюер")
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
    flat = bridge.replace_range({"paragraph": 3}, "перевод", language="ru-RU",
                                flatten=True, doc=doc)
    print(flat)
    check("went ahead", flat.get("success"), True)
    check("reported the comment it dropped", flat.get("comments_dropped"), 1)
    check("no comments left", bridge.list_comments(doc=doc)["count"], 0)

    print("\n--- a comment on a point, with no text under it ---")
    point = bridge.add_comment({"paragraph": 3, "offset": 3, "length": 0},
                               "здесь", doc=doc)
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
    check("saying what it removed", removed.get("content"), "здесь")
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
                               "про Alpha", author="Ревьюер", doc=doc)
    second = bridge.add_comment({"paragraph": 3}, "про весь абзац",
                                author="Клод", doc=doc)
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
    check("which one", section["comments"][0]["content"], "про весь абзац")
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
          "про Alpha")

    print("\n--- editing a comment, not the document ---")
    target = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"][0]
    changed = bridge.update_comment(target["id"], text="переформулировано",
                                    doc=doc)
    print(changed)
    check("changed", changed.get("success"), True)
    check("reporting what changed", changed.get("changed"), ["text"])
    after = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"][0]
    check("the new text is there", after["content"], "переформулировано")
    check("the author is untouched", after["author"], "Ревьюер")
    check("it is the same comment", after["id"], target["id"])
    check("the document text is untouched",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"],
          "Alpha beta alpha.")
    check("its anchor still covers the same words",
          after["anchor_text"], "Alpha")

    resolved = bridge.update_comment(target["id"], resolved=True, author="Клод",
                                     doc=doc)
    check("resolved and reassigned", resolved.get("success"), True)
    settled = bridge.list_comments({"paragraph": 1}, doc=doc)["comments"][0]
    check("resolved", settled["resolved"], True)
    check("reassigned", settled["author"], "Клод")
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
    saved_at = "/tmp/mcp_live_comment_ids.odt"
    doc.storeToURL(f"file://{saved_at}", ())
    reopened = desktop.loadComponentFromURL(f"file://{saved_at}", "_blank", 0, ())
    reloaded = bridge.list_comments(doc=reopened)
    print(reloaded)
    check("the comment came back", reloaded.get("count"), 1)
    check("with the same id", reloaded["comments"][0]["id"], survivor["id"])
    check("with the same text", reloaded["comments"][0]["content"],
          survivor["content"])
    check("editing it by that id works after reopening",
          bridge.update_comment(survivor["id"], text="после перезагрузки",
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
                                    "про query", doc=doc)
    check("the anchor is the term", first_note.get("anchor_text"), "query")
    check("an offset past one comment still lands right",
          (at(0, 5), at(6, 2), at(10, 3)), ("query", "is", "he "))

    second_note = bridge.add_comment({"paragraph": 1, "offset": 6, "length": 2},
                                     "про is", doc=doc)
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
    picture_file = write_test_png("/tmp/mcp_live_source.png")
    source = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    source.Name, source.Value = "URL", f"file://{picture_file}"

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
                description="схема запроса")
    listed = bridge.list_images(doc=doc)
    print(listed)
    check("the document holds one picture", listed.get("count"), 1)
    picture = listed["images"][0]
    check("named", picture["name"], "Schema")
    check("inline in the text", picture["inline"], True)
    check("with the address of its anchor", picture["address"],
          {"paragraph": 1, "offset": 6, "length": 0})
    check("the text it is anchored to", picture["paragraph_text"],
          "query is the entry point")
    check("its size in millimetres", (picture["width_mm"], picture["height_mm"]),
          (24.3, 24.5))
    check("its size in pixels", picture["pixels"], {"width": 8, "height": 8})
    check("its title", picture["title"], "GraphQL schema")
    check("its alternative text", picture["description"], "схема запроса")

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
    written = bridge.export_image("Schema", path="/tmp/mcp_live_export.png",
                                  doc=doc)
    print({k: v for k, v in written.items() if k != "_image_content"})
    check("written", written.get("success"), True)
    check("as a PNG", open("/tmp/mcp_live_export.png", "rb").read(8),
          b"\x89PNG\r\n\x1a\x0a")
    check("of the size it reports", written.get("bytes"),
          os.path.getsize("/tmp/mcp_live_export.png"))
    check("carrying the address of the picture", written.get("address"),
          {"paragraph": 1, "offset": 6, "length": 0})
    inline_result = bridge.export_image("Schema", path="/tmp/mcp_live_inline.png",
                                        inline=True, doc=doc)
    check("handed back for looking at", inline_result.get("inline"), True)
    check("as base64 of a PNG",
          inline_result["_image_content"]["data"].startswith("iVBORw0KGgo"),
          True)
    check("an unknown picture is refused",
          bridge.export_image("Nope", doc=doc).get("success"), False)
    for leftover in ("/tmp/mcp_live_export.png", "/tmp/mcp_live_inline.png"):
        os.unlink(leftover)

    print("\n--- a rewrite that would destroy it is refused ---")
    refused = bridge.replace_range({"paragraph": 1}, "перевод", doc=doc)
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
    after = bridge.list_images(doc=doc)
    check("the picture is there", after.get("count"), 1)
    check("with its anchor where the text put it",
          after["images"][0]["address"], {"paragraph": 1, "offset": 7,
                                          "length": 0})
    check("and its own name", after["images"][0]["name"], "Schema")

    print("\n--- flatten=true destroys it, and says so ---")
    flattened = bridge.replace_range({"paragraph": 1}, "перевод целиком",
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
    allowed = bridge.replace_range({"paragraph": 1}, "перевод", flatten=True,
                                   doc=doc)
    check("the rewrite went ahead", allowed.get("success"), True)
    check("nothing was reported destroyed", allowed.get("images_dropped"), 0)
    check("and it survived", bridge.list_images(doc=doc)["count"], 1)
    os.unlink(picture_file)

    print("\n--- a replacement over a point is refused, not written ---")
    before_text = bridge.read_paragraphs(start=1, count=1,
                                         doc=doc)["paragraphs"][0]["text"]
    caret = body.createTextCursorByRange(bridge._paragraph_at(body, 1).getStart())
    caret.goRight(3, False)
    doc.getCurrentController().select(caret)
    point = bridge.replace_range({"selection": True}, "ПРОБА", doc=doc)
    print(point)
    check("refused", point.get("success"), False)
    check("saying there is nothing to replace",
          "nothing to replace" in point["error"], True)
    explicit = bridge.replace_range({"paragraph": 1, "offset": 3, "length": 0},
                                    "ПРОБА", doc=doc)
    check("an explicit zero length too", explicit.get("success"), False)
    check("and nothing was written",
          bridge.read_paragraphs(start=1, count=1,
                                 doc=doc)["paragraphs"][0]["text"], before_text)

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

    first = bridge.render_page(page=1, dpi=90, path="/tmp/mcp_live_page1.png",
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

    second = bridge.render_page(page=2, dpi=90, path="/tmp/mcp_live_page2.png",
                                inline=False, doc=doc)
    check("the second page rendered too", second.get("success"), True)
    check("and it is a different page",
          open("/tmp/mcp_live_page1.png", "rb").read()
          != open("/tmp/mcp_live_page2.png", "rb").read(), True)
    check("the reader's cursor is back on page 1", view.getPage(), 1)

    by_address = bridge.render_page(address={"paragraph": 3}, dpi=60,
                                    path="/tmp/mcp_live_addr.png",
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
    through_draw = bridge._render_through_draw(doc, 2, "/tmp/mcp_live_draw.png",
                                               744, 1052)
    check("the fallback wrote a picture too", through_draw is not None, True)
    if through_draw:
        check("as a PNG", open(through_draw, "rb").read(8),
              b"\x89PNG\r\n\x1a\x0a")
        os.unlink(through_draw)
    for leftover in ("/tmp/mcp_live_page1.png", "/tmp/mcp_live_page2.png",
                     "/tmp/mcp_live_addr.png"):
        os.unlink(leftover)

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
