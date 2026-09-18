"""Fakes for page styles: the page itself, and the headers it carries.

Everything here was measured on a live Writer, and the awkward parts are the
point:

  * while `HeaderIsOn` is false, `HeaderIsShared`, `FirstIsShared` and
    `HeaderHeight` answer **None**, not a value;
  * switching a header off **throws its text away** — off and on again left
    both sides empty;
  * with the header shared, `HeaderText` and `HeaderTextLeft` are the same
    text; with it unshared, `HeaderText` follows the **right** page;
  * a page measurement **rounds**: A4 answers 21001 rather than 21000, and a
    margin written as 1500 reads back as 1499, because it goes through
    twips. Nothing may compare one for equality;
  * `IsLandscape` is a flag and nothing more — setting it leaves the width
    and the height exactly where they were.
"""

from tests.fakes_values import FakeSize

PAGE_STYLES = ("Standard", "First Page", "Left Page", "Right Page",
               "Envelope", "Index", "HTML", "Footnote", "Endnote",
               "Landscape")

DISPLAY_NAMES = {"Standard": "Default Page Style"}


def _rounded(hundredths):
    """What a length becomes on the way through Writer's twips."""
    if not isinstance(hundredths, int) or isinstance(hundredths, bool):
        return hundredths
    twips = round(hundredths * 72.0 / 127.0)
    return int(round(twips * 127.0 / 72.0))


class FakeColumns:
    """com.sun.star.text.TextColumns, which counts 0 until it is given one."""

    def __init__(self, count=0):
        self._count = count

    def getColumnCount(self):
        return self._count

    def setColumnCount(self, count):
        # Measured: a page style told to use one column still answers 0.
        self._count = 0 if count <= 1 else count


class FakeHeaderText:
    """The XText of one header or footer: a line, and the fields in it."""

    def __init__(self):
        self._text = ""

    def getString(self):
        return self._text

    def setString(self, value):
        self._text = value

    def createTextCursor(self):
        return self

    def insertString(self, cursor, value, absorb):
        self._text += value

    def insertTextContent(self, cursor, content, absorb):
        self._text += content.getPresentation(False)

    def createEnumeration(self):
        from tests.fakes_values import FakeEnumeration
        return FakeEnumeration([])


class FakePageStyle:
    """com.sun.star.style.PageStyle, as the bridge touches one."""

    def __init__(self, name):
        object.__setattr__(self, "Name", name)
        object.__setattr__(self, "DisplayName", DISPLAY_NAMES.get(name, name))
        object.__setattr__(self, "_on", {"Header": False, "Footer": False})
        object.__setattr__(self, "_shared", {"Header": True, "Footer": True})
        object.__setattr__(self, "_first_shared", True)
        object.__setattr__(self, "_texts", {})
        object.__setattr__(self, "_page", {
            "Width": _rounded(21000), "Height": _rounded(29700),
            "IsLandscape": False, "TopMargin": 2000, "BottomMargin": 2000,
            "LeftMargin": 2000, "RightMargin": 2000, "GutterMargin": 0,
            "FollowStyle": "Standard", "NumberingType": 4,
            "PageStyleLayout": "ALL"})
        object.__setattr__(self, "_columns", FakeColumns())

    # -- the header buffers -------------------------------------------

    def _buffer(self, stem, which):
        key = (stem, which)
        if key not in self._texts:
            self._texts[key] = FakeHeaderText()
        return self._texts[key]

    def _resolve(self, stem, which):
        shared = self._shared[stem]
        if which == "first" and not self._first_shared:
            return self._buffer(stem, "first")
        if which == "first":
            which = "all"
        if shared:
            return self._buffer(stem, "all")
        if which == "all":
            which = "right"
        return self._buffer(stem, which)

    def _share(self, stem, shared):
        """Turning sharing off leaves both sides saying what the one said."""
        if bool(shared) == self._shared[stem]:
            return
        if not shared:
            was = self._buffer(stem, "all").getString()
            for side in ("left", "right"):
                self._buffer(stem, side).setString(was)
        self._shared[stem] = bool(shared)

    def _switch(self, stem, on):
        if not on:
            # Measured: Writer keeps nothing of a header switched off.
            for key in list(self._texts):
                if key[0] == stem:
                    self._texts[key].setString("")
        self._on[stem] = bool(on)

    # -- what the bridge reads and writes ------------------------------

    def __getattr__(self, prop):
        page = object.__getattribute__(self, "_page")
        if prop in page:
            return page[prop]
        if prop == "TextColumns":
            return object.__getattribute__(self, "_columns")
        if prop == "Size":
            return FakeSize(page["Width"], page["Height"])
        for stem in ("Header", "Footer"):
            if prop == f"{stem}IsOn":
                return self._on[stem]
            if prop in (f"{stem}IsShared", f"{stem}Height",
                        f"{stem}BodyDistance"):
                if not self._on[stem]:
                    return None      # measured: None, not a value
                return self._shared[stem] if prop.endswith("IsShared") else 0
            if prop.startswith(f"{stem}Text"):
                suffix = prop[len(f"{stem}Text"):].lower() or "all"
                if not self._on[stem]:
                    return FakeHeaderText()
                return self._resolve(stem, suffix)
        if prop == "FirstIsShared":
            if not (self._on["Header"] or self._on["Footer"]):
                return None
            return self._first_shared
        raise AttributeError(prop)

    def __setattr__(self, prop, value):
        page = object.__getattribute__(self, "_page")
        if prop in page:
            page[prop] = (_rounded(value)
                          if prop not in ("NumberingType", "IsLandscape",
                                          "FollowStyle", "PageStyleLayout")
                          else value)
            return
        if prop == "Size":
            page["Width"] = _rounded(value.Width)
            page["Height"] = _rounded(value.Height)
            return
        if prop == "TextColumns":
            object.__setattr__(self, "_columns", value)
            return
        for stem in ("Header", "Footer"):
            if prop == f"{stem}IsOn":
                self._switch(stem, value)
                return
            if prop == f"{stem}IsShared":
                self._share(stem, value)
                return
        if prop == "FirstIsShared":
            object.__setattr__(self, "_first_shared", bool(value))
            return
        object.__setattr__(self, prop, value)

    # -- what describe_style asks of any style -------------------------

    _DESCRIBED = ("Width", "Height", "IsLandscape", "TopMargin",
                  "BottomMargin", "LeftMargin", "RightMargin", "GutterMargin",
                  "TextColumns", "NumberingType", "PageStyleLayout",
                  "FollowStyle", "HeaderIsOn", "FooterIsOn")

    def getPropertySetInfo(self):
        from tests.fakes_values import FakeProperties
        return FakeProperties(sorted(self._DESCRIBED))

    def getPropertyState(self, prop):
        from tests.fakes_values import FakeEnum
        if prop not in self._DESCRIBED:
            raise RuntimeError(f"no property {prop}")
        # A page style sets its own page: nothing here is inherited.
        return FakeEnum("DIRECT_VALUE")

    def isInUse(self):
        # Measured: a headless document that plainly used a page style
        # reported every one of them unused, which is why nothing relies on
        # this.
        return False

    def isUserDefined(self):
        return self.Name not in PAGE_STYLES


class FakeLineNumbering:
    """doc.LineNumberingProperties, whose Separator throws on read."""

    def __init__(self):
        self.IsOn = False
        self.Interval = 5
        self.Distance = 499
        self.RestartAtEachPage = False
        self.CountEmptyLines = True
        self.NumberingType = 4

    @property
    def Separator(self):
        raise RuntimeError("Separator")
