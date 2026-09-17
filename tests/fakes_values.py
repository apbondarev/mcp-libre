"""The small things a UNO call hands back: locales, enums, sizes, structs.

A pyuno enum stringifies as a wrapper rather than as its value, and a fake
that handed back a plain string once hid a defect where every run read as
upright — so FakeEnum behaves like the real thing.
"""




# Deliberately NOT subclassing the com.sun.star.* interfaces: a real UNO object
# is <class 'pyuno'> and satisfies isinstance against none of them, so faking
# documents that way would hide exactly the bug it should catch. Real code has
# to ask supportsService().
WRITER_SERVICES = frozenset({
    "com.sun.star.text.TextDocument",
    "com.sun.star.text.GenericTextDocument",
})


CALC_SERVICES = frozenset({"com.sun.star.sheet.SpreadsheetDocument"})


class FakeLocale:
    """com.sun.star.lang.Locale, as much of it as the bridge touches."""

    def __init__(self, language="en", country="US"):
        self.Language = language
        self.Country = country
        self.Variant = ""

    def __eq__(self, other):
        return (self.Language, self.Country) == (
            getattr(other, "Language", None), getattr(other, "Country", None))

    def __repr__(self):
        return f"FakeLocale({self.Language}-{self.Country})"


class FakeEnum:
    """A pyuno enum: it stringifies as a wrapper, never as its own value.

    The bridge has to read `.value`; a fake that handed back a plain string
    hid a defect where every run was reported upright.
    """

    def __init__(self, value):
        self.value = value

    def __str__(self):
        return f"<Enum instance com.sun.star.awt.FontSlant ('{self.value}')>"


# The bytes a PNG filter writes in the fakes: a real, if tiny, PNG.
FakeGraphicProviderPNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c630001000005000101"
    "0d0a2db40000000049454e44ae426082")


class FakeProperty:
    """A com.sun.star.beans.PropertyValue as a plain pair."""

    def __init__(self, name, value):
        self.Name = name
        self.Value = value


class FakeSize:
    """com.sun.star.awt.Size, in whatever unit the caller means."""

    def __init__(self, width, height):
        self.Width = width
        self.Height = height


class FakeNameAccess:
    """com.sun.star.container.XNameAccess over things that carry a Name.

    An item names itself either with a Name attribute or with getName(), the
    way a bookmark does.
    """

    def __init__(self, items):
        self.items = list(items)

    @staticmethod
    def _name_of(item):
        if hasattr(item, "getName"):
            return item.getName()
        return item.Name

    def getElementNames(self):
        return tuple(self._name_of(item) for item in self.items)

    def hasByName(self, name):
        return any(self._name_of(item) == name for item in self.items)

    def getByName(self, name):
        for item in self.items:
            if self._name_of(item) == name:
                return item
        raise RuntimeError(f"no element named {name}")


class FakeDateTime:
    """com.sun.star.util.DateTime, the struct an annotation's date is."""

    def __init__(self, year=2026, month=9, day=8, hours=9, minutes=32,
                 seconds=29):
        self.Year = year
        self.Month = month
        self.Day = day
        self.Hours = hours
        self.Minutes = minutes
        self.Seconds = seconds
        self.NanoSeconds = 0
        self.IsUTC = False


class FakeSpellChecker:
    """Stands in for com.sun.star.linguistic2.SpellChecker.

    Knows a fixed vocabulary per language, so a test can say what is a word
    and what is not without shipping a dictionary.
    """

    def __init__(self, vocabulary=None, suggestions=None, locales=("ru-RU", "en-US")):
        self.vocabulary = vocabulary or {}
        self.suggestions = suggestions or {}
        self.locales = set(locales)
        self.checked = []

    def hasLocale(self, locale):
        return f"{locale.Language}-{locale.Country}" in self.locales

    def isValid(self, word, locale, properties):
        tag = f"{locale.Language}-{locale.Country}"
        self.checked.append((word, tag))
        return word in self.vocabulary.get(tag, ())

    def spell(self, word, locale, properties):
        if self.isValid(word, locale, properties):
            return None
        return FakeSpellAlternatives(self.suggestions.get(word, []))


class FakeSpellAlternatives:
    def __init__(self, alternatives):
        self._alternatives = list(alternatives)

    def getAlternatives(self):
        return tuple(self._alternatives)


class FakeLineSpacing:
    """com.sun.star.style.LineSpacing: a mode and a height."""

    def __init__(self, mode=0, height=100):
        self.Mode = mode
        self.Height = height


class FakeBorderLine:
    """com.sun.star.table.BorderLine2, as a style reports one."""

    def __init__(self, width=0, colour=0):
        self.LineWidth = width
        self.Color = colour
        self.LineStyle = 0
        self.InnerLineWidth = 0
        self.OuterLineWidth = width
        self.LineDistance = 0


class FakeProperties:
    """What getPropertySetInfo().getProperties() hands back."""

    def __init__(self, names):
        self._names = list(names)

    def getProperties(self):
        return tuple(FakeProperty(name, None) for name in self._names)


class FakeCount:
    def __init__(self, count):
        self._count = count

    def getCount(self):
        return self._count


class FakeSeparator:
    def __init__(self, position):
        self.Position = position
        self.IsVisible = True


class FakeEnumeration:
    def __init__(self, items):
        self._items = list(items)

    def hasMoreElements(self):
        return bool(self._items)

    def nextElement(self):
        return self._items.pop(0)


class FakeComponents:
    """What Desktop.getComponents() hands back: an enumeration of documents."""

    def __init__(self, documents):
        self._documents = list(documents)

    def createEnumeration(self):
        return FakeEnumeration(self._documents)
