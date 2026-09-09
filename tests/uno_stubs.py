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


class FakeServiceManager:
    def __init__(self):
        self.graphic_provider = FakeGraphicProvider()

    def createInstanceWithContext(self, name, ctx):
        if name == "com.sun.star.graphic.GraphicProvider":
            return self.graphic_provider
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
        uno.Enum = lambda type_name, value: f"{type_name}.{value}"
        # uno.Any wraps a value in a typed Any for a UNO call; a fake only
        # needs the value to arrive, so it passes straight through.
        uno.Any = lambda type_name, value: value

        _STRUCT_FIELDS = {
            "com.sun.star.lang.Locale": ("Language", "Country", "Variant"),
            "com.sun.star.beans.PropertyValue": ("Name", "Handle", "Value", "State"),
            "com.sun.star.util.DateTime": ("NanoSeconds", "Seconds", "Minutes",
                                           "Hours", "Day", "Month", "Year",
                                           "IsUTC"),
            "com.sun.star.table.BorderLine2": ("Color", "InnerLineWidth",
                                               "OuterLineWidth", "LineDistance",
                                               "LineStyle", "LineWidth"),
        }

        def create_uno_struct(name, *args):
            fields = _STRUCT_FIELDS.get(name, ())
            struct = types.SimpleNamespace(**{field: "" for field in fields})
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

    for module_name, interfaces in _INTERFACES.items():
        if module_name in sys.modules:
            continue
        module = types.ModuleType(module_name)
        for interface in interfaces:
            setattr(module, interface, type(interface, (), {}))
        sys.modules[module_name] = module

    if str(PLUGIN_PYTHONPATH) not in sys.path:
        sys.path.insert(0, str(PLUGIN_PYTHONPATH))
