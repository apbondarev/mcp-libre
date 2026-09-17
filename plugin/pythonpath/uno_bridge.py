"""
The bridge between MCP tools and LibreOffice, assembled from its parts.

Everything that touches UNO lives behind `UNOBridge`, and this file is only
where its parts meet: each part is a mixin in a module of its own, named
after the thing in the document it works on. One object, several files —
`bridge.read_runs(...)` reaches the same method it always did.

Where a method lives:

    uno_values.py      colours, locales, property helpers — no documents
    uno_address.py     the address model, and the two directions between an
                       address and a UNO range; the table lookups a cell
                       address needs
    uno_anchors.py     anchors: a held cursor that keeps pointing at a place
                       while the paragraphs around it move
    uno_documents.py   documents themselves: active, open, saved, closed,
                       renamed, exported
    uno_view.py        the caret, the selection, the page
    uno_reading.py     paragraphs, headings, search
    uno_editing.py     inserting and replacing text, under _guarded_edit
    uno_formatting.py  character and paragraph formatting, and styles
    uno_runs.py        the formatted pieces a stretch is made of
    uno_comments.py    the notes in the margin
    uno_fields.py      the bits that write themselves: dates, page numbers
    uno_redlines.py    the changes a document is keeping, and settling them
    uno_tables.py      tables: reading, placing, dressing
    uno_images.py      pictures
    uno_spelling.py    spelling
    uno_rendering.py   a page as a picture

Nothing in uno_values imports from the rest, and the parts call each other
only through `self`, which is what lets them sit in separate files at all.
"""

import uno
import logging

# Re-exported: callers and tests import these from here, where they have
# always been.
from uno_values import (AddressError, MAX_OUTLINE_ENTRIES,  # noqa: F401
                        MAX_PARAGRAPH_COUNT, MAX_TEXT_CHARS)
from uno_address import AddressMixin
from uno_anchors import AnchorsMixin
from uno_documents import DocumentsMixin
from uno_view import ViewMixin
from uno_reading import ReadingMixin
from uno_editing import EditingMixin
from uno_formatting import FormattingMixin
from uno_runs import RunsMixin
from uno_comments import CommentsMixin
from uno_fields import FieldsMixin
from uno_redlines import RedlinesMixin
from uno_tables import TablesMixin
from uno_images import ImagesMixin
from uno_spelling import SpellingMixin
from uno_rendering import RenderingMixin

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class UNOBridge(RenderingMixin, SpellingMixin, ImagesMixin, TablesMixin,
                RedlinesMixin, FieldsMixin, CommentsMixin, RunsMixin,
                FormattingMixin, EditingMixin, ReadingMixin, ViewMixin,
                DocumentsMixin, AnchorsMixin, AddressMixin):
    """Bridge between MCP operations and LibreOffice UNO API"""

    def __init__(self):
        """Initialize the UNO bridge"""
        try:
            self.ctx = uno.getComponentContext()
            self.smgr = self.ctx.ServiceManager
            self.desktop = self.smgr.createInstanceWithContext(
                "com.sun.star.frame.Desktop", self.ctx)
            logger.info("UNO Bridge initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize UNO Bridge: {e}")
            raise
