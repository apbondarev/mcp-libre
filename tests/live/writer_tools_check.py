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
