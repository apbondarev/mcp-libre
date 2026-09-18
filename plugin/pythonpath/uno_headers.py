"""Headers and footers: what every page carries, per page style.

They belong to a **page style**, not to the text, which is why a running
title in the wrong language survives a translation of the body: nothing in
the body is what a reader is looking at up there. Measured on a live Writer:

  * `HeaderIsOn` has to be true before anything else answers. While it is
    false, `HeaderIsShared`, `FirstIsShared` and `HeaderHeight` all read as
    **None** rather than as a value;
  * **switching a header off destroys its text.** `HeaderIsOn = False` and
    then True again left both the left and the right header empty — so
    removing one is a destructive act and says so, and nothing turns a
    header off to "reset" it;
  * `HeaderIsShared` true (the default) makes one header for every page, and
    `HeaderText` and `HeaderTextLeft` are then the same text; with it false,
    `HeaderTextLeft` and `HeaderTextRight` differ and `HeaderText` follows
    the **right** one. `FirstIsShared = False` is what gives a first page a
    header of its own;
  * a header's paragraphs are an ordinary `XText` — paragraph style "Header",
    portions, fields and all — so a page number in a footer is a
    `TextField.PageNumber` inserted into it, and `doc.getTextFields()` does
    report the fields that live there. Their anchors are **outside the body
    text**, so they have no paragraph address;
  * `isInUse()` on a page style answered False for every style in a headless
    document that plainly used one, so it is reported and never relied upon.
"""

from typing import Any, Dict, List, Optional
import logging
import re

from uno_values import _get_property, _text_payload, refusal

logger = logging.getLogger(__name__)

PARTS = ("header", "footer")
WHICH = ("all", "left", "right", "first")

# What a caller can put in the text of a header, in place of a field.
PLACEHOLDERS = {
    "page": ("PageNumber", {"NumberingType": 4}),
    "pages": ("PageCount", {"NumberingType": 4}),
    "title": ("DocInfo.Title", {}),
    "date": ("DateTime", {"IsDate": True, "IsFixed": False}),
    "author": ("Author", {}),
    "file": ("FileName", {}),
}
PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")


class HeadersMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _page_styles(self, doc: Any) -> Any:
        try:
            return doc.StyleFamilies.getByName("PageStyles")
        except Exception as e:
            logger.info(f"This document keeps no page styles: {e}")
            return None

    def _page_style(self, doc: Any, name: Optional[str]):
        """(style, name, refusal) — the page style a call is about"""
        styles = self._page_styles(doc)
        if styles is None:
            return None, None, refusal("UNSUPPORTED",
                                       "this document keeps no page styles")
        if name is None:
            name = self._current_page_style(doc) or "Standard"
        if not styles.hasByName(name):
            return None, None, refusal(
                "NOT_FOUND",
                f"no page style called {name!r}; list_headers_footers says "
                f"which there are")
        return styles.getByName(name), name, None

    def _current_page_style(self, doc: Any) -> Optional[str]:
        """The page style the caret is on, which is the one a caller means"""
        try:
            return doc.getCurrentController().getViewCursor().PageStyleName
        except Exception as e:
            logger.info(f"Could not ask which page style is in use: {e}")
            return None

    def _part_text(self, style: Any, part: str, which: str) -> Any:
        """The XText of one header or footer — 'all' means the shared one"""
        stem = "Header" if part == "header" else "Footer"
        suffix = {"all": "", "left": "Left", "right": "Right",
                  "first": "First"}[which]
        return getattr(style, f"{stem}Text{suffix}")

    def _describe_part(self, style: Any, part: str) -> Dict[str, Any]:
        stem = "Header" if part == "header" else "Footer"
        on = bool(_get_property(style, f"{stem}IsOn", False))
        described = {"on": on}
        if not on:
            # Measured: while it is off, every other property answers None.
            return described
        shared = _get_property(style, f"{stem}IsShared", True)
        first_shared = _get_property(style, "FirstIsShared", True)
        described["same_on_both_pages"] = bool(shared)
        described["same_on_the_first_page"] = bool(first_shared)
        texts = {}
        for which in WHICH:
            if which == "left" and shared:
                continue
            if which == "right" and shared:
                continue
            if which == "first" and first_shared:
                continue
            if which == "all" and not shared:
                continue
            try:
                texts[which] = _text_payload(
                    self._part_text(style, part, which).getString())["text"]
            except Exception as e:
                logger.info(f"Could not read the {which} {part}: {e}")
        described["text"] = texts
        return described

    def list_headers_footers(self, page_style: Optional[str] = None,
                             doc: Any = None) -> Dict[str, Any]:
        """
        The headers and footers of a document, by page style

        A running title lives here and not in the text, which is why a
        translated document still shows the old one. `in_use` is what the
        office says about the style and is not to be trusted on its own — a
        headless document that plainly used a style reported every style
        unused.
        """
        doc, error = self._writer_document(doc, "Listing headers and footers")
        if error:
            return error
        styles = self._page_styles(doc)
        if styles is None:
            return refusal("UNSUPPORTED", "this document keeps no page styles")

        wanted = [page_style] if page_style else list(styles.getElementNames())
        if page_style and not styles.hasByName(page_style):
            return refusal("NOT_FOUND",
                           f"no page style called {page_style!r}; this "
                           f"document has "
                           f"{', '.join(styles.getElementNames())}")

        current = self._current_page_style(doc)
        found = []
        for name in wanted:
            try:
                style = styles.getByName(name)
            except Exception as e:
                logger.info(f"Could not read the page style {name}: {e}")
                continue
            described = {"page_style": name,
                         "display_name": _get_property(style, "DisplayName",
                                                       name),
                         "current": name == current,
                         "header": self._describe_part(style, "header"),
                         "footer": self._describe_part(style, "footer")}
            try:
                described["in_use"] = bool(style.isInUse())
            except Exception as e:
                logger.info(f"A page style would not say whether it is used: {e}")
                described["in_use"] = None
            found.append(described)

        # A style with nothing on it is noise unless it was asked for by name.
        if not page_style:
            found = [one for one in found
                     if one["header"]["on"] or one["footer"]["on"]
                     or one["current"]]
        return {"success": True, "page_styles": found, "count": len(found),
                "current_page_style": current}

    def set_header_footer(self, part: str, text: str,
                          page_style: Optional[str] = None,
                          which: str = "all",
                          same_on_both_pages: Optional[bool] = None,
                          same_on_the_first_page: Optional[bool] = None,
                          track_changes: Optional[bool] = None,
                          doc: Any = None) -> Dict[str, Any]:
        """
        Write a header or a footer of a page style

        `{page}`, `{pages}`, `{title}`, `{date}`, `{author}` and `{file}` in
        the text become the fields a reader expects, so "Страница {page} из
        {pages}" is one call and stays right as the document grows.
        """
        doc, error = self._writer_document(doc, "Writing a header or footer")
        if error:
            return error
        if part not in PARTS:
            return refusal("INVALID_PARAMETER",
                           f"part is \"header\" or \"footer\", got {part!r}")
        if which not in WHICH:
            return refusal("INVALID_PARAMETER",
                           f"which is one of {', '.join(WHICH)}, got "
                           f"{which!r}")
        if not isinstance(text, str):
            return refusal("INVALID_PARAMETER",
                           f"text must be a string, got "
                           f"{type(text).__name__}")

        style, name, refused = self._page_style(doc, page_style)
        if refused:
            return refused
        stem = "Header" if part == "header" else "Footer"

        def edit():
            # Nothing else on a header answers until it is switched on.
            setattr(style, f"{stem}IsOn", True)
            if same_on_both_pages is not None:
                setattr(style, f"{stem}IsShared", bool(same_on_both_pages))
            if same_on_the_first_page is not None:
                style.FirstIsShared = bool(same_on_the_first_page)
            if which in ("left", "right") \
                    and _get_property(style, f"{stem}IsShared", True):
                # Asking for one side means the two sides differ.
                setattr(style, f"{stem}IsShared", False)
            if which == "first" and _get_property(style, "FirstIsShared", True):
                style.FirstIsShared = False

            target = self._part_text(style, part, which)
            written, fields = self._write_with_fields(doc, target, text)
            return {"part": part, "page_style": name, "which": which,
                    "text": _text_payload(written)["text"],
                    "fields": fields,
                    "same_on_both_pages": bool(
                        _get_property(style, f"{stem}IsShared", True)),
                    "same_on_the_first_page": bool(
                        _get_property(style, "FirstIsShared", True))}

        return self._guarded_edit(doc, f"MCP: set the {part}", track_changes,
                                  edit)

    def _write_with_fields(self, doc: Any, target: Any,
                           text: str) -> tuple:
        """Write the text, turning {page} and its kind into real fields"""
        target.setString("")
        cursor = target.createTextCursor()
        written: List[str] = []
        fields: List[str] = []
        position = 0
        for match in PLACEHOLDER.finditer(text):
            before = text[position:match.start()]
            if before:
                target.insertString(cursor, before, False)
                written.append(before)
            kind = match.group(1)
            service, settings = PLACEHOLDERS[kind]
            try:
                field = doc.createInstance(
                    f"com.sun.star.text.TextField.{service}")
                for key, value in settings.items():
                    try:
                        setattr(field, key, value)
                    except Exception as e:
                        logger.info(f"Could not set {key} on {kind}: {e}")
                if kind == "page":
                    try:
                        from com.sun.star.text.PageNumberType import CURRENT
                        field.SubType = CURRENT
                    except Exception as e:
                        logger.info(f"Could not ask for this page: {e}")
                target.insertTextContent(cursor, field, False)
                fields.append(kind)
            except Exception as e:
                logger.info(f"Could not put a {kind} field in: {e}")
                target.insertString(cursor, match.group(0), False)
            position = match.end()
        rest = text[position:]
        if rest:
            target.insertString(cursor, rest, False)
            written.append(rest)
        try:
            doc.getTextFields().refresh()
        except Exception as e:
            logger.info(f"Could not refresh the fields: {e}")
        return target.getString(), fields

    def remove_header_footer(self, part: str,
                             page_style: Optional[str] = None,
                             track_changes: Optional[bool] = None,
                             doc: Any = None) -> Dict[str, Any]:
        """
        Switch a header or footer off, which throws its text away

        Measured: turning a header off and on again leaves it empty, on both
        sides — Writer does not keep what was there. The text that is about
        to go comes back in the result, since nothing else will have it.
        """
        doc, error = self._writer_document(doc, "Removing a header or footer")
        if error:
            return error
        if part not in PARTS:
            return refusal("INVALID_PARAMETER",
                           f"part is \"header\" or \"footer\", got {part!r}")

        style, name, refused = self._page_style(doc, page_style)
        if refused:
            return refused
        stem = "Header" if part == "header" else "Footer"
        was = self._describe_part(style, part)
        if not was["on"]:
            return refusal("NOT_FOUND",
                           f"the {part} of {name!r} is already off")

        def edit():
            setattr(style, f"{stem}IsOn", False)
            return {"removed": part, "page_style": name,
                    "was_saying": was.get("text", {})}

        return self._guarded_edit(doc, f"MCP: remove the {part}",
                                  track_changes, edit)
