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

# Interfaces uno_bridge imports by name, per module.
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


class FakeServiceManager:
    def createInstanceWithContext(self, name, ctx):
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
