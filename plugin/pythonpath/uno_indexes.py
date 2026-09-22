"""Tables of contents and the other indexes a document builds from itself.

A table of contents is not text a writer keeps up to date: it is a
`com.sun.star.text.ContentIndex` that reads the document and writes itself
on `update()`. Measured, and every part of it matters to a caller:

  * **an index's entries are body paragraphs.** A table of contents inserted
    into the document above costs one paragraph, and updating it turns that
    into four — a title and three entries — so every address below an index
    moves when it is updated. That is the one thing to know before mixing
    indexes with addressed edits: update the indexes last, or read the
    addresses again afterwards;
  * `CreateFromOutline` defaults to **False** and `CreateFromMarks` to True,
    so a content index inserted with UNO's own defaults lists nothing at all.
    `insert_index` turns the outline on, since a table of contents built from
    the headings is what the words mean;
  * the index names itself — "Table of Contents1", "Alphabetical Index1" —
    and `doc.getDocumentIndexes()` hands them out under those names, which is
    how one is named for updating or removing;
  * its paragraphs wear "Contents Heading", "Contents 1", "Contents 2" …,
    and `IsProtected` is True, so a reader cannot type over them;
  * a heading rewritten and then updated comes through: the entry read
    "Introduction" before and "Введение" after, which is the whole point;
  * removing one takes its paragraphs with it —
    `anchor.getText().removeTextContent(index)`, nineteen paragraphs down to
    fifteen;
  * an index mark is `com.sun.star.text.DocumentIndexMark` with a
    `PrimaryKey`, inserted over a range with absorb **True**, which keeps the
    text it marks.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import AddressError, _get_property, _text_payload, refusal

logger = logging.getLogger(__name__)

# The kinds a caller can ask for, in words, and the service each is.
INDEX_KINDS = {
    "contents": "ContentIndex",
    "alphabetical": "DocumentIndex",
    "illustrations": "IllustrationsIndex",
    "tables": "TableIndex",
    "objects": "ObjectIndex",
    "user": "UserIndex",
    "bibliography": "Bibliography",
}

# What an index found in a document is. UserIndex answers with
# "UserDefinedIndex", which is not the name it is created under — measured.
KIND_OF_SERVICE = {
    "com.sun.star.text.ContentIndex": "contents",
    "com.sun.star.text.DocumentIndex": "alphabetical",
    "com.sun.star.text.IllustrationsIndex": "illustrations",
    "com.sun.star.text.TableIndex": "tables",
    "com.sun.star.text.ObjectIndex": "objects",
    "com.sun.star.text.UserDefinedIndex": "user",
    "com.sun.star.text.Bibliography": "bibliography",
}

PICK = ("Say which indexes in exactly one way: name for a single one, or "
        "all=true for every index in the document")


class IndexesMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _indexes(self, doc: Any) -> Any:
        try:
            return doc.getDocumentIndexes()
        except Exception as e:
            logger.info(f"This document keeps no indexes: {e}")
            return None

    def _index_kind(self, index: Any) -> Optional[str]:
        try:
            services = list(index.getSupportedServiceNames())
        except Exception as e:
            logger.info(f"An index would not name itself: {e}")
            return None
        return next((KIND_OF_SERVICE[one] for one in services
                     if one in KIND_OF_SERVICE), None)

    def _describe_index(self, index: Any, name: str,
                        address: Any = None) -> Dict[str, Any]:
        lines = []
        try:
            lines = index.getAnchor().getString().replace("\r\n", "\n").split("\n")
        except Exception as e:
            logger.info(f"Could not read the index {name}: {e}")
        described = {
            "name": name,
            "kind": self._index_kind(index),
            "title": _get_property(index, "Title", None),
            "address": address,
            "paragraphs": len(lines),
            "entries": max(0, len(lines) - 1),
            "protected": bool(_get_property(index, "IsProtected", False)),
        }
        if described["kind"] == "contents":
            described["from_outline"] = bool(
                _get_property(index, "CreateFromOutline", False))
            described["from_marks"] = bool(
                _get_property(index, "CreateFromMarks", False))
            described["levels"] = _get_property(index, "Level", None)
        return described

    def list_indexes(self, number: bool = False,
                     doc: Any = None) -> Dict[str, Any]:
        """
        The tables of contents and other indexes a document has

        Each says what it is built from, how many entries it holds now and
        where it sits, since an index takes body paragraphs of its own.
        """
        doc, error = self._writer_document(doc, "Listing indexes")
        if error:
            return error
        indexes = self._indexes(doc)
        if indexes is None:
            return refusal("UNSUPPORTED", "this document keeps no indexes")

        names, held, anchors = [], [], []
        for name in indexes.getElementNames():
            try:
                index = indexes.getByName(name)
                anchors.append(index.getAnchor())
            except Exception as e:
                logger.info(f"Could not read the index {name}: {e}")
                continue
            names.append(name)
            held.append(index)
        placed = self._place_all(doc, anchors, number)

        found = [self._describe_index(index, name, address)
                 for name, index, address in zip(names, held, placed)]
        found.sort(key=lambda one: (one["address"] or {}).get("paragraph")
                   if (one["address"] or {}).get("paragraph") is not None
                   else 10 ** 9)
        return {"success": True, "indexes": found, "count": len(found)}

    def _body_paragraph_count(self, doc: Any) -> int:
        return sum(1 for _paragraph, _index in self._body_paragraphs(doc))

    def insert_index(self, address: Any, kind: str = "contents",
                     title: Optional[str] = None,
                     levels: Optional[int] = None,
                     from_outline: bool = True, from_marks: bool = False,
                     track_changes: Optional[bool] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Put a table of contents or another index before the paragraph an
        address names

        It is written at once, so it is never inserted empty, and the result
        says how many body paragraphs the document gained — every address
        below it has moved by that much.
        """
        doc, error = self._writer_document(doc, "Inserting an index")
        if error:
            return error
        if kind not in INDEX_KINDS:
            return refusal("INVALID_PARAMETER",
                           f"kind is one of {', '.join(sorted(INDEX_KINDS))}, "
                           f"got {kind!r}")
        if levels is not None and (isinstance(levels, bool)
                                   or not isinstance(levels, int)
                                   or not 1 <= levels <= 10):
            return refusal("INVALID_PARAMETER",
                           f"levels counts heading levels, 1 to 10, got "
                           f"{levels!r}")

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        before = self._body_paragraph_count(doc)

        def edit():
            index = doc.createInstance(
                f"com.sun.star.text.{INDEX_KINDS[kind]}")
            if title is not None:
                index.Title = title
            if kind == "contents":
                # UNO's own default builds a table of contents from index
                # marks and *not* from the headings, which lists nothing in
                # a document nobody has marked up — measured.
                index.CreateFromOutline = bool(from_outline)
                index.CreateFromMarks = bool(from_marks)
                if levels is not None:
                    index.Level = levels
            owner = target.getText()
            owner.insertTextContent(target.getStart(), index, False)
            index.update()
            after = self._body_paragraph_count(doc)
            name = _get_property(index, "Name", None) or ""
            located, _, _ = self._locate_range(doc, index.getAnchor())
            described = self._describe_index(index, name, located)
            described["paragraphs_added"] = after - before
            return described

        return self._guarded_edit(doc, f"MCP: insert a {kind} index",
                                  track_changes, edit)

    def update_indexes(self, name: Optional[str] = None, all: bool = False,
                       track_changes: Optional[bool] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Write the indexes again from what the document says now

        A translated heading shows in the old language until this is run.
        Updating moves every address below an index, so the result says how
        many body paragraphs the document has gained or lost.
        """
        doc, error = self._writer_document(doc, "Updating indexes")
        if error:
            return error
        if bool(name) == bool(all):
            return refusal("INVALID_PARAMETER", PICK)
        indexes = self._indexes(doc)
        if indexes is None:
            return refusal("UNSUPPORTED", "this document keeps no indexes")
        if name and not indexes.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no index called {name!r}; list_indexes says "
                           f"which there are")

        wanted = [name] if name else list(indexes.getElementNames())
        if not wanted:
            return refusal("NOT_FOUND",
                           "this document has no indexes to update")
        before = self._body_paragraph_count(doc)

        def edit():
            updated = []
            for one in wanted:
                index = indexes.getByName(one)
                index.update()
                updated.append(self._describe_index(index, one))
            after = self._body_paragraph_count(doc)
            return {"updated": [one["name"] for one in updated],
                    "indexes": updated,
                    "paragraphs_before": before, "paragraphs_after": after,
                    "paragraphs_moved": after - before}

        return self._guarded_edit(doc, "MCP: update indexes", track_changes,
                                  edit)

    def delete_index(self, name: str, doc: Any = None) -> Dict[str, Any]:
        """Take an index away, and the paragraphs it wrote with it"""
        doc, error = self._writer_document(doc, "Deleting an index")
        if error:
            return error
        indexes = self._indexes(doc)
        if indexes is None or not indexes.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no index called {name!r}; list_indexes says "
                           f"which there are")
        index = indexes.getByName(name)
        described = self._describe_index(index, name)

        def edit():
            anchor = index.getAnchor()
            anchor.getText().removeTextContent(index)
            return {"deleted": name, "kind": described["kind"],
                    "title": described["title"],
                    "paragraphs_removed": described["paragraphs"]}

        return self._guarded_edit(doc, "MCP: delete an index", None, edit)

    def add_index_mark(self, address: Any, key: str,
                       secondary_key: Optional[str] = None,
                       text: Optional[str] = None,
                       track_changes: Optional[bool] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Mark a piece of text for the alphabetical index

        The text itself is left alone: a mark covers it the way a bookmark
        does, and the index built from the marks lists it under the key.
        """
        doc, error = self._writer_document(doc, "Marking text for an index")
        if error:
            return error
        if not isinstance(key, str) or not key.strip():
            return refusal("INVALID_PARAMETER",
                           "a mark needs a key — the word the index lists it "
                           "under")

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        def edit():
            mark = doc.createInstance("com.sun.star.text.DocumentIndexMark")
            mark.PrimaryKey = key.strip()
            if secondary_key:
                mark.SecondaryKey = secondary_key
            if text:
                mark.AlternativeText = text
            owner = target.getText()
            owner.insertTextContent(target, mark, True)
            marked = ""
            try:
                marked = mark.getAnchor().getString()
            except Exception as e:
                logger.info(f"Could not read what a mark covers: {e}")
            located, _, _ = self._locate_range(doc, mark.getAnchor())
            return {"key": key.strip(), "secondary_key": secondary_key,
                    "alternative_text": text,
                    "marked": _text_payload(marked)["text"],
                    "address": located}

        return self._guarded_edit(doc, f"MCP: mark {key!r} for the index",
                                  track_changes, edit)
