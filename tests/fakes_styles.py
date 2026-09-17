"""Styles that inherit like real ones.

The bridge asks getPropertyState to tell a style's own definition from what
it inherits — DIRECT_VALUE against DEFAULT_VALUE — so a fake that answered
one flat dictionary of properties could not show the difference the whole
describe_style tool rests on.
"""

from tests.fakes_values import (FakeBorderLine, FakeEnum, FakeLineSpacing,
                                FakeLocale, FakeProperties)


# What a style gives when nothing in its chain says otherwise, and what the
# built-in styles define themselves. The values for "Text body" are the ones
# a live LibreOffice reports as DIRECT_VALUE, which are exactly its
# definition in styles.xml — margins in 1/100 mm, weight where 150 is bold.
FAKE_STYLE_DEFAULTS = {
    "CharFontName": "Liberation Serif", "CharHeight": 12.0,
    "CharWeight": 100.0, "CharPosture": FakeEnum("NONE"), "CharUnderline": 0,
    "CharColor": -1, "CharBackColor": -1,
    "CharLocale": None,                     # filled in per style below
    "ParaAdjust": 0, "ParaLineSpacing": None,
    "ParaTopMargin": 0, "ParaBottomMargin": 0, "ParaLeftMargin": 0,
    "ParaRightMargin": 0, "ParaFirstLineIndent": 0,
    "ParaContextMargin": False, "ParaKeepTogether": False, "ParaSplit": True,
    "ParaOrphans": 0, "ParaWidows": 0, "ParaBackColor": -1,
    "FillStyle": FakeEnum("NONE"), "FillColor": -1,
    "TopBorder": None, "BottomBorder": None, "LeftBorder": None,
    "RightBorder": None, "NumberingStyleName": "", "OutlineLevel": 0,
    "PageDescName": "", "BreakType": FakeEnum("NONE"),
    "FollowStyle": "", "LinkStyle": "", "Category": 0, "IsAutoUpdate": False,
    "Hidden": False, "DisplayName": "",
}


FAKE_STYLE_PARENTS = {"Standard": "", "Text body": "Standard",
                      "Heading": "Standard", "Heading 1": "Heading",
                      "Heading 2": "Heading", "Heading 3": "Heading",
                      "Comment": "Standard", "Preformatted Text": "Standard",
                      "Quotations": "Standard", "List": "Standard",
                      "Caption": "Standard"}


FAKE_STYLE_OWN = {
    "Text body": {"FollowStyle": "Text body", "LinkStyle": "",
                  "ParaBottomMargin": 247, "ParaBottomMarginRelative": 100,
                  "ParaContextMargin": False, "ParaTopMargin": 0,
                  "ParaTopMarginRelative": 100,
                  "ParaLineSpacing": FakeLineSpacing(0, 115)},
    "Heading": {"FollowStyle": "Text body", "ParaTopMargin": 423,
                "ParaBottomMargin": 212, "ParaKeepTogether": True},
    "Heading 2": {"CharHeight": 14.0, "CharWeight": 150.0, "OutlineLevel": 2,
                  "FollowStyle": "Text body"},
    "Preformatted Text": {"CharFontName": "Liberation Mono",
                          "ParaAdjust": 0},
    "Comment": {"CharHeight": 10.0},
}


class FakeStyle:
    """A style that inherits like a real one and says what it sets itself.

    The bridge asks `getPropertyState` to tell a style's own definition from
    what it inherits — DIRECT_VALUE against DEFAULT_VALUE — so a fake that
    answered one flat dict of properties could not show the difference that
    the whole tool rests on.
    """

    _INTERNALS = {"name", "Name", "_own", "_family", "_styles", "_display"}

    def __init__(self, name, locale=None, family="ParagraphStyles",
                 styles=None):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "Name", name)
        object.__setattr__(self, "_family", family)
        object.__setattr__(self, "_styles", styles)
        own = dict(FAKE_STYLE_OWN.get(name, {}))
        if locale is not None:
            own["CharLocale"] = locale
        object.__setattr__(self, "_own", own)

    # --- inheritance ------------------------------------------------------
    @property
    def ParentStyle(self):
        return FAKE_STYLE_PARENTS.get(self.name,
                                      "" if self.name == "Standard"
                                      else "Standard")

    def _chain(self):
        chain, name = [], self.ParentStyle
        while name and len(chain) < 10:
            chain.append(name)
            name = FAKE_STYLE_PARENTS.get(name, "")
        return chain

    def _inherited(self, prop):
        for name in self._chain():
            own = FAKE_STYLE_OWN.get(name, {})
            if prop in own:
                return True, own[prop]
        if prop in FAKE_STYLE_DEFAULTS:
            value = FAKE_STYLE_DEFAULTS[prop]
            if prop == "CharLocale" and value is None:
                value = FakeLocale("en", "US")
            if prop == "ParaLineSpacing" and value is None:
                value = FakeLineSpacing(0, 100)
            if prop.endswith("Border") and value is None:
                value = FakeBorderLine(0, 0)
            if prop == "DisplayName" and not value:
                value = self.name
            return True, value
        return False, None

    def __getattr__(self, prop):
        own = object.__getattribute__(self, "_own")
        if prop in own:
            return own[prop]
        known, value = self._inherited(prop)
        if known:
            return value
        raise AttributeError(prop)

    def __setattr__(self, prop, value):
        if prop in self._INTERNALS or prop.startswith("_"):
            object.__setattr__(self, prop, value)
            return
        self._own[prop] = value          # writing a property sets it here

    # --- what the bridge asks about ---------------------------------------
    def getPropertySetInfo(self):
        names = set(FAKE_STYLE_DEFAULTS) | set(self._own)
        for name in self._chain():
            names |= set(FAKE_STYLE_OWN.get(name, {}))
        return FakeProperties(sorted(names))

    def getPropertyState(self, prop):
        if prop in self._own:
            return FakeEnum("DIRECT_VALUE")
        known, _value = self._inherited(prop)
        if not known:
            raise RuntimeError(f"no property {prop}")
        return FakeEnum("DEFAULT_VALUE")

    def isUserDefined(self):
        return self.name not in FAKE_STYLE_PARENTS

    def isInUse(self):
        return True


class FakeStyleFamily:
    def __init__(self, names):
        self.names = list(names)
        self.styles = {}

    def hasByName(self, name):
        return name in self.names

    def getElementNames(self):
        return tuple(self.names)

    def getByName(self, name):
        if name not in self.names:
            raise RuntimeError(f"no style {name}")
        return self.styles.setdefault(name, FakeStyle(name, styles=self))


class FakeStyleFamilies:
    """doc.StyleFamilies, with the families a Writer document has."""

    def __init__(self, families=None):
        self._built = {}
        self.families = families or {
            "ParagraphStyles": ["Standard", "Heading", "Text body",
                                "Heading 1", "Heading 2", "Heading 3",
                                "Preformatted Text", "Quotations", "Comment",
                                "List", "Caption", "Table Contents",
                                "Table Heading",
                                # The caption styles a fresh Writer document
                                # carries, one per standard sequence.
                                "Figure", "Illustration", "Table", "Text",
                                "Drawing"],
            "CharacterStyles": ["Default Style", "Emphasis", "Source Text"],
        }

    def getElementNames(self):
        return tuple(self.families)

    def hasByName(self, name):
        return name in self.families

    def getByName(self, name):
        if name not in self.families:
            raise RuntimeError(f"no style family {name}")
        if name not in self._built:
            self._built[name] = FakeStyleFamily(self.families[name])
        return self._built[name]
