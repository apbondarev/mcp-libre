"""Fake UNO modules so plugin code can be imported outside LibreOffice.

`uno` and the `com.sun.star.*` hierarchy only exist inside LibreOffice's
bundled Python, so `plugin/pythonpath/uno_bridge.py` is unimportable in this
repo's venv. Registering stand-ins in sys.modules makes it importable, which
lets the bridge's own logic be tested without a running LibreOffice.

The interface classes must be real classes: uno_bridge dispatches on
isinstance(doc, XTextDocument), so fake documents subclass them.
"""

import sys
import types
from pathlib import Path

PLUGIN_PYTHONPATH = Path(__file__).resolve().parent.parent / "plugin" / "pythonpath"

# Interfaces the plugin modules import by name, per module.
# Constant groups the bridge imports by name. The values are the real ones,
# read from a running LibreOffice — a stub that made them up would let a
# wrong constant pass here and fail in an office.
# An enum constant in pyuno is an object carrying its name in `.value`, not
# an integer — `from com.sun.star.style.BreakType import PAGE_BEFORE` hands
# one out, while importing the group throws "No module named 'com'".
_ENUMS = {
    "com.sun.star.style.BreakType": ("NONE", "COLUMN_BEFORE", "COLUMN_AFTER",
                                     "COLUMN_BOTH", "PAGE_BEFORE",
                                     "PAGE_AFTER", "PAGE_BOTH"),
}

_CONSTANTS = {
    "com.sun.star.text.ControlCharacter": {
        "PARAGRAPH_BREAK": 0, "LINE_BREAK": 1, "HARD_HYPHEN": 2,
        "SOFT_HYPHEN": 3, "HARD_SPACE": 4, "APPEND_PARAGRAPH": 5},
    "com.sun.star.text.SetVariableType": {
        "VAR": 0, "SEQUENCE": 1, "FORMULA": 2, "STRING": 3},
}

_INTERFACES = {
    "com.sun.star.beans": ["PropertyValue"],
    "com.sun.star.text": ["XTextDocument"],
    "com.sun.star.sheet": ["XSpreadsheetDocument"],
    "com.sun.star.presentation": ["XPresentationDocument"],
    "com.sun.star.document": ["XDocumentEventListener"],
    "com.sun.star.awt": ["XActionListener"],
    # registration.py, the UNO component itself
    "com.sun.star.frame": ["XDispatchProvider", "XDispatch"],
    "com.sun.star.lang": ["XServiceInfo"],
}


class FakeGraphicProvider:
    """com.sun.star.graphic.GraphicProvider, enough of it to write a file.

    Writes a real (tiny) PNG so a test can check that a file appeared, how
    big it is and what it begins with, without a running LibreOffice.
    """

    PNG = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082")

    def __init__(self):
        self.written = []

    def storeGraphic(self, graphic, properties):
        from urllib.parse import unquote, urlparse

        settings = {prop.Name: prop.Value for prop in properties}
        target = urlparse(settings.get("URL", ""))
        path = unquote(target.path)
        with open(path, "wb") as handle:
            handle.write(self.PNG)
        self.written.append((path, settings.get("MimeType")))


class FakeDispatchHelper:
    """com.sun.star.frame.DispatchHelper, as the review tools use it.

    There is no accept or reject on the document model — measured — so a
    change is settled by selecting it and sending a command. What that does
    to the *text* is LibreOffice's business and is checked in
    tests/live/writer_tools_check.py; what it does to the bookkeeping is
    modelled here: the change under the selection stops being a change.
    """

    def __init__(self):
        self.sent = []

    def executeDispatch(self, frame, command, target, flags, arguments):
        self.sent.append(command)
        document = getattr(frame, "document", None)
        if command == ".uno:ConvertTableToText":
            # Writer's own way back, and its Delimiter must be a string —
            # measured: with the tab's number the dispatch did nothing.
            delimiter = next((one.Value for one in arguments
                              if one.Name == "Delimiter"), "\t")
            self._table_to_text(document, delimiter)
            return
        if command in (".uno:MoveDown", ".uno:MoveUp"):
            # There is no move on the model either: Writer's own command
            # carries the paragraph with everything on it, which the fake
            # models by moving the line and what hangs off it.
            self._move(document, command.endswith("Down"))
            return
        redlines = getattr(document, "redlines", None)
        if redlines is None:
            return
        if command.endswith("AllTrackedChanges"):
            redlines.entries = []
            return
        selection = document.getCurrentController().getSelection()
        try:
            chosen = selection.getByIndex(0)
            span = (chosen.start, chosen.end)
        except Exception:
            return
        redlines.entries = [
            one for one in redlines.entries
            if not (one.RedlineStart.start == span[0]
                    and one.RedlineEnd.start == span[1])]

    def _table_to_text(self, document, delimiter):
        """The table the caret is in becomes paragraphs, cells joined."""
        if document is None or not isinstance(delimiter, str):
            return
        model = document.getText()
        view = document.getCurrentController().getViewCursor()
        table = next((one for one in getattr(document, "tables", [])
                      if any(one.getCellByName(name) is not None
                             for name in one.getCellNames())), None)
        if table is None:
            return
        lines = []
        rows = {}
        for name in table.getCellNames():
            column, number = name[0], int(name[1:])
            rows.setdefault(number, []).append((column, name))
        for number in sorted(rows):
            cells = [table.getCellByName(name).getString()
                     for _column, name in sorted(rows[number])]
            lines.append(delimiter.join(cells))
        at = getattr(table, "after_paragraph", 0)
        for offset, line in enumerate(lines):
            model._insert_paragraph_at(at + offset, line)
        model.enumeration_items = [item for item in model.enumeration_items
                                   if item is not table]
        document.tables = [one for one in document.tables if one is not table]
        view.start = view.end = (at, 0)

    def _move(self, document, down):
        """Move the selected paragraphs one step, as .uno:MoveDown does."""
        if document is None:
            return
        model = document.getText()
        # A move acts on the **view cursor**, which in a real office is the
        # selection a reader sees; the fake keeps the two apart, so this asks
        # the view the way Writer does.
        view = document.getCurrentController().getViewCursor()
        try:
            first, last = view.start[0], view.end[0]
        except Exception:
            return
        target = last + 1 if down else first - 1
        if target < 0 or target >= len(model.paragraphs):
            return
        block = list(range(first, last + 1))
        moving = [model.take_paragraph(index) for index in reversed(block)]
        moving.reverse()
        landing = (first + 1) if down else (first - 1)
        for offset, piece in enumerate(moving):
            model.put_paragraph(landing + offset, piece)


class FakeServiceManager:
    def __init__(self):
        self.graphic_provider = FakeGraphicProvider()
        self.dispatcher = FakeDispatchHelper()

    def createInstanceWithContext(self, name, ctx):
        if name == "com.sun.star.graphic.GraphicProvider":
            return self.graphic_provider
        if name == "com.sun.star.frame.DispatchHelper":
            return self.dispatcher
        return object()


class FakeComponentContext:
    ServiceManager = FakeServiceManager()


def install_uno_stubs():
    """Register the fake modules and put plugin/pythonpath on sys.path.

    Idempotent, so several test modules can call it.
    """
    if "uno" not in sys.modules:
        uno = types.ModuleType("uno")
        uno.getComponentContext = lambda: FakeComponentContext()
        def make_enum(type_name, value):
            """A pyuno enum: it carries its name in `.value`, not as a string.

            The stub used to hand back "com.sun.star.awt.FontSlant.ITALIC",
            which no code reading `.value` could use — and reading `.value`
            is exactly what the bridge has to do.
            """
            from tests.fakes_values import FakeEnum
            return FakeEnum(value)

        uno.Enum = make_enum
        # uno.Any wraps a value in a typed Any for a UNO call; a fake only
        # needs the value to arrive, so it passes straight through.
        uno.Any = lambda type_name, value: value

        _STRUCT_FIELDS = {
            "com.sun.star.lang.Locale": ("Language", "Country", "Variant"),
            "com.sun.star.beans.PropertyValue": ("Name", "Handle", "Value", "State"),
            "com.sun.star.util.DateTime": ("NanoSeconds", "Seconds", "Minutes",
                                           "Hours", "Day", "Month", "Year",
                                           "IsUTC"),
            "com.sun.star.awt.Size": ("Width", "Height"),
            "com.sun.star.table.BorderLine2": ("Color", "InnerLineWidth",
                                               "OuterLineWidth", "LineDistance",
                                               "LineStyle", "LineWidth"),
        }

        def create_uno_struct(name, *args):
            fields = _STRUCT_FIELDS.get(name, ())
            # A size is a pair of numbers, and a fake that started it at ""
            # would hand the layout tools a string to arithmetic on.
            blank = 0 if name == "com.sun.star.awt.Size" else ""
            struct = types.SimpleNamespace(**{field: blank for field in fields})
            for field, value in zip(fields, args):
                setattr(struct, field, value)
            return struct

        uno.createUnoStruct = create_uno_struct
        sys.modules["uno"] = uno

    if "unohelper" not in sys.modules:
        unohelper = types.ModuleType("unohelper")

        class Base:
            def __init__(self, *args, **kwargs):
                pass

        class ImplementationHelper:
            def __init__(self):
                self.implementations = []

            def addImplementation(self, creator, name, services):
                self.implementations.append((creator, name, services))

        unohelper.Base = Base
        unohelper.ImplementationHelper = ImplementationHelper
        sys.modules["unohelper"] = unohelper

    for package in ("com", "com.sun", "com.sun.star"):
        if package not in sys.modules:
            sys.modules[package] = types.ModuleType(package)

    for module_name, names in _ENUMS.items():
        if module_name in sys.modules:
            continue
        from tests.fakes_values import FakeEnum
        module = types.ModuleType(module_name)
        for name in names:
            setattr(module, name, FakeEnum(name))
        sys.modules[module_name] = module

    for module_name, values in _CONSTANTS.items():
        if module_name in sys.modules:
            continue
        module = types.ModuleType(module_name)
        for name, value in values.items():
            setattr(module, name, value)
        sys.modules[module_name] = module

    for module_name, interfaces in _INTERFACES.items():
        if module_name in sys.modules:
            continue
        module = types.ModuleType(module_name)
        for interface in interfaces:
            setattr(module, interface, type(interface, (), {}))
        sys.modules[module_name] = module

    if str(PLUGIN_PYTHONPATH) not in sys.path:
        sys.path.insert(0, str(PLUGIN_PYTHONPATH))
