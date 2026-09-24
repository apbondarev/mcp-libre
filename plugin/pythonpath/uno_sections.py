"""Sections: the named regions a document is divided into.

A section is how Writer carries a protected region, content linked from
another file, and columns that differ from the rest of the page. Measured on
a live Writer, and two of the measurements matter more than the tools:

  * **a section does not change the paragraph numbering.** Six body
    paragraphs before one was made over two of them, six after, and the same
    while the section was hidden — `IsVisible = False` leaves its paragraphs
    in the enumeration and readable, so hidden text is still addressed and
    still read. Nothing in an address has to know about sections;
  * **protection does not protect.** `IsProtected = True` stops a *reader*
    typing into the section, and nothing else: `replace_range` wrote straight
    through it, and so did a bare `setString`. UNO raises nothing and reports
    nothing. So the refusal is ours — `_protected_section_over` is asked
    before text is rewritten, and a caller that means it can say
    `protected=false` first, or pass `allow_protected=true`.

Beside those: a section is named by a plain `Name` property that can be
written to rename it in place; sections nest, and `ParentSection` /
`ChildSections` say how; `TextColumns.getColumnCount()` is **0** on a section
nobody has given columns to, so one column is reported as one; `FileLink` is
a struct of `FileURL` and `FilterName` with `LinkRegion` beside it; and
removing a section leaves every paragraph it held.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import AddressError, _get_property, _text_payload, refusal

logger = logging.getLogger(__name__)


class SectionsMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _sections(self, doc: Any) -> Any:
        try:
            return doc.getTextSections()
        except Exception as e:
            logger.info(f"This document keeps no sections: {e}")
            return None

    def _section_columns(self, section: Any) -> int:
        """How many columns a section is set in — one when it has none.

        Measured: a section nobody has given columns to answers 0, which is
        not a number of columns anybody means.
        """
        try:
            return max(1, section.TextColumns.getColumnCount())
        except Exception as e:
            logger.info(f"A section would not say its columns: {e}")
            return 1

    def _describe_section(self, section: Any, name: str,
                          address: Any = None) -> Dict[str, Any]:
        link = _get_property(section, "FileLink", None)
        url = _get_property(link, "FileURL", "") if link else ""
        described = {
            "name": name,
            "address": address,
            "protected": bool(_get_property(section, "IsProtected", False)),
            "visible": bool(_get_property(section, "IsVisible", True)),
            "columns": self._section_columns(section),
            "condition": _get_property(section, "Condition", "") or "",
            "linked_file": url or None,
            "linked_region": _get_property(section, "LinkRegion", "") or None,
        }
        parent = _get_property(section, "ParentSection", None)
        described["inside"] = _get_property(parent, "Name", None) if parent \
            else None
        children = _get_property(section, "ChildSections", None) or []
        described["holds"] = [_get_property(child, "Name", "")
                              for child in children]
        try:
            described["text"] = _text_payload(
                section.getAnchor().getString())["text"]
        except Exception as e:
            logger.info(f"Could not read the section {name}: {e}")
            described["text"] = None
        return described

    def list_sections(self, address: Any = None,
                      number: bool = False,
                      doc: Any = None) -> Dict[str, Any]:
        """
        The named sections of a document, with what each one is for

        A section says whether it is protected, whether it is hidden, how
        many columns it is set in and whether its content is linked from
        another file — which is how a caller learns why a part of a document
        behaves differently from the rest.
        """
        doc, error = self._writer_document(doc, "Listing sections")
        if error:
            return error
        sections = self._sections(doc)
        if sections is None:
            return refusal("UNSUPPORTED", "this document keeps no sections")

        try:
            covers, scope = (self._comment_scope(doc, address) if number
                             else self._scope_over(doc, address))
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        # A section covers whole paragraphs and usually several of them, so
        # "which sections is paragraph 2 in?" is a question about overlap,
        # not about where a section starts — the scope the comments use would
        # answer only with the sections that begin in that paragraph.
        overlaps = self._overlap_test(doc, address)

        names, held, anchors = [], [], []
        for name in sections.getElementNames():
            try:
                section = sections.getByName(name)
                anchor = section.getAnchor()
            except Exception as e:
                logger.info(f"Could not read the section {name}: {e}")
                continue
            # Thrown out **before** it is anchored and described: a real
            # guide holds 117 sections, and describing one is a dozen
            # property reads. The scope's answer was three of them.
            if not number:
                if overlaps is not None:
                    if not overlaps(anchor):
                        continue
                elif address is not None and not covers(anchor):
                    continue
            names.append(name)
            held.append(section)
            anchors.append(anchor)
        placed = self._place_all(doc, anchors, number)

        found = []
        for name, section, located, anchor in zip(names, held, placed, anchors):
            described = self._describe_section(section, name, located)
            if number:
                if overlaps is not None:
                    if not overlaps(anchor):
                        continue
                elif not covers(described["address"]):
                    continue
            found.append(described)
        if number:
            found.sort(key=lambda one:
                       ((one["address"] or {}).get("paragraph")
                        if (one["address"] or {}).get("paragraph") is not None
                        else 10 ** 9,
                        (one["address"] or {}).get("offset") or 0))
        return {"success": True, "sections": found, "count": len(found),
                "order": "reading" if number
                         else "as the document names them",
                "protected": sum(1 for one in found if one["protected"]),
                "hidden": sum(1 for one in found if not one["visible"]),
                "scope": scope}

    def _overlap_test(self, doc: Any, address: Any):
        """A test for "does this range overlap the address?", or None.

        None means the address is not a range this can be asked of — a
        heading names a section of the outline, not a stretch — and the
        caller falls back to the scope the comments use.
        """
        if address is None or (isinstance(address, dict)
                               and "heading" in address):
            return None
        try:
            asked = self._resolve_address(doc, address)
            body = doc.getText()
            start, end = asked.getStart(), asked.getEnd()
        except Exception as e:
            logger.info(f"Could not resolve the address to compare: {e}")
            return None

        def overlaps(anchor):
            try:
                # compareRegionStarts answers 1 when the first starts before
                # the second and 0 when they start together, so ">= 0" reads
                # as "at or before".
                return (body.compareRegionStarts(anchor.getStart(), end) >= 0
                        and body.compareRegionStarts(start, anchor.getEnd()) >= 0)
            except Exception as e:
                logger.info(f"Could not compare a section with a range: {e}")
                return False

        return overlaps

    # ---- the protection UNO does not enforce -------------------------

    def _protected_section_over(self, doc: Any, span: Any) -> Optional[str]:
        """The name of a protected section holding this range, if any.

        Measured: `IsProtected` stops the reader's keyboard and nothing else —
        the API writes straight through it, silently. So every tool that
        rewrites text asks this first, and a document with no sections at all
        pays two UNO calls for the answer.
        """
        sections = self._sections(doc)
        if sections is None:
            return None
        try:
            names = list(sections.getElementNames())
        except Exception as e:
            logger.info(f"Could not list the sections: {e}")
            return None
        if not names:
            return None

        body = doc.getText()
        for name in names:
            try:
                section = sections.getByName(name)
                if not _get_property(section, "IsProtected", False):
                    continue
                anchor = section.getAnchor()
                # compareRegionStarts is the only comparison that works
                # across pyuno proxies, and it throws for a range in another
                # text — a cell, say — which means "not in this section".
                starts_before = body.compareRegionStarts(anchor, span) >= 0
                ends_after = body.compareRegionEnds(anchor, span) <= 0
                if starts_before and ends_after:
                    return name
            except Exception as e:
                logger.info(f"Could not compare against section {name}: {e}")
                continue
        return None

    def _refuse_protected(self, doc: Any, span: Any,
                          allowed: bool) -> Optional[Dict[str, Any]]:
        """A refusal when a range sits in a protected section, or None"""
        if allowed:
            return None
        name = self._protected_section_over(doc, span)
        if not name:
            return None
        return refusal(
            "READ_ONLY",
            f"that text is inside {name!r}, a protected section — Writer stops "
            f"a reader typing there, and this stops a tool. Unprotect it with "
            f"update_section, or pass allow_protected=true to write anyway",
            section=name)

    # ---- making and changing them ------------------------------------

    def create_section(self, address: Any, name: str,
                       protected: bool = False, visible: bool = True,
                       columns: Optional[int] = None,
                       track_changes: Optional[bool] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Make a named section of the paragraphs an address covers

        The paragraphs stay where they are and keep their numbers — a section
        is a region of the body text, not a container that moves anything.
        """
        doc, error = self._writer_document(doc, "Creating a section")
        if error:
            return error
        if not isinstance(name, str) or not name.strip():
            return refusal("INVALID_PARAMETER", "a section needs a name")
        name = name.strip()
        sections = self._sections(doc)
        if sections is not None and sections.hasByName(name):
            return refusal("INVALID_PARAMETER",
                           f"this document already has a section called "
                           f"{name!r}")
        if columns is not None and (isinstance(columns, bool)
                                    or not isinstance(columns, int)
                                    or not 1 <= columns <= 99):
            return refusal("INVALID_PARAMETER",
                           f"columns is a number from 1 to 99, got {columns!r}")

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        def edit():
            section = doc.createInstance("com.sun.star.text.TextSection")
            section.Name = name
            owner = target.getText()
            owner.insertTextContent(target, section, True)
            if columns is not None and columns > 1:
                self._set_columns(doc, section, columns)
            # Protection and visibility are set after the section exists, so
            # that a protected section can still be made over text in one go.
            section.IsVisible = bool(visible)
            section.IsProtected = bool(protected)
            located, _, _ = self._locate_range(doc, section.getAnchor())
            return self._describe_section(section, section.Name, located)

        return self._guarded_edit(doc, f"MCP: section {name}", track_changes,
                                  edit)

    def _set_columns(self, doc: Any, section: Any, columns: int) -> None:
        settings = doc.createInstance("com.sun.star.text.TextColumns")
        settings.setColumnCount(columns)
        section.TextColumns = settings

    def update_section(self, name: str, new_name: Optional[str] = None,
                       protected: Optional[bool] = None,
                       visible: Optional[bool] = None,
                       columns: Optional[int] = None,
                       condition: Optional[str] = None,
                       track_changes: Optional[bool] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Protect a section, hide it, rename it or set its columns

        The text inside is left alone: this changes what the section is, not
        what it says.
        """
        doc, error = self._writer_document(doc, "Updating a section")
        if error:
            return error
        sections = self._sections(doc)
        if sections is None or not sections.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no section called {name!r}; list_sections says "
                           f"which there are")
        if all(one is None for one in (new_name, protected, visible, columns,
                                       condition)):
            return refusal("INVALID_PARAMETER",
                           "say what to change: new_name, protected, visible, "
                           "columns or condition")
        if new_name is not None:
            if not isinstance(new_name, str) or not new_name.strip():
                return refusal("INVALID_PARAMETER", "a section needs a name")
            new_name = new_name.strip()
            if new_name != name and sections.hasByName(new_name):
                return refusal("INVALID_PARAMETER",
                               f"this document already has a section called "
                               f"{new_name!r}")
        if columns is not None and (isinstance(columns, bool)
                                    or not isinstance(columns, int)
                                    or not 1 <= columns <= 99):
            return refusal("INVALID_PARAMETER",
                           f"columns is a number from 1 to 99, got {columns!r}")

        section = sections.getByName(name)
        was = self._describe_section(section, name)

        def edit():
            if columns is not None:
                self._set_columns(doc, section, columns)
            if condition is not None:
                section.Condition = condition
            if visible is not None:
                section.IsVisible = bool(visible)
            if protected is not None:
                section.IsProtected = bool(protected)
            if new_name is not None:
                section.Name = new_name
            located, _, _ = self._locate_range(doc, section.getAnchor())
            described = self._describe_section(section, section.Name, located)
            described["was"] = {key: was[key] for key in
                                ("name", "protected", "visible", "columns")}
            return described

        return self._guarded_edit(doc, "MCP: update a section", track_changes,
                                  edit)

    def delete_section(self, name: str, track_changes: Optional[bool] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Take a section away, leaving every paragraph it held

        Measured: the text stays exactly where it was and keeps its numbers —
        only the region around it goes.
        """
        doc, error = self._writer_document(doc, "Deleting a section")
        if error:
            return error
        sections = self._sections(doc)
        if sections is None or not sections.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no section called {name!r}; list_sections says "
                           f"which there are")
        section = sections.getByName(name)
        described = self._describe_section(section, name)

        def edit():
            anchor = section.getAnchor()
            anchor.getText().removeTextContent(section)
            return {"deleted": name, "kept_text": described["text"],
                    "was_protected": described["protected"],
                    "held": described["holds"]}

        return self._guarded_edit(doc, "MCP: delete a section", track_changes,
                                  edit)
