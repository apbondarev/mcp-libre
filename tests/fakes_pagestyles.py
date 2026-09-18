"""Fakes for page styles, and the headers and footers they carry.

Everything here was measured on a live Writer, and the awkward parts are the
point:

  * while `HeaderIsOn` is false, `HeaderIsShared`, `FirstIsShared` and
    `HeaderHeight` answer **None**, not a value;
  * switching a header off **throws its text away** — off and on again left
    both sides empty;
  * with the header shared, `HeaderText` and `HeaderTextLeft` are the same
    text; with it unshared, `HeaderText` follows the **right** page.
"""

PAGE_STYLES = ("Standard", "First Page", "Left Page", "Right Page",
               "Envelope", "Index", "HTML", "Footnote", "Endnote",
               "Landscape")

DISPLAY_NAMES = {"Standard": "Default Page Style"}


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
    """com.sun.star.style.PageStyle, as far as headers and footers go."""

    def __init__(self, name):
        object.__setattr__(self, "Name", name)
        object.__setattr__(self, "DisplayName",
                           DISPLAY_NAMES.get(name, name))
        object.__setattr__(self, "_on", {"Header": False, "Footer": False})
        object.__setattr__(self, "_shared", {"Header": True, "Footer": True})
        object.__setattr__(self, "_first_shared", True)
        object.__setattr__(self, "_texts", {})

    # -- the buffers --------------------------------------------------

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
            # Measured: Writer keeps nothing of a header it has been told to
            # switch off.
            for key in list(self._texts):
                if key[0] == stem:
                    self._texts[key].setString("")
        self._on[stem] = bool(on)

    # -- what the bridge reads and writes ------------------------------

    def __getattr__(self, prop):
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

    def isInUse(self):
        # Measured: a headless document that plainly used a page style
        # reported every one of them unused, which is why nothing relies on
        # this.
        return False

    def isUserDefined(self):
        return self.Name not in PAGE_STYLES
