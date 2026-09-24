"""Fields: the bits of a document that write themselves.

A date, a page number, the document's title — Writer keeps them as fields and
redraws them, and a reader sees only what they currently say. Measured, and
it is what makes them dangerous to the text tools:

  * a field is a portion of type `TextField`, and unlike a comment's marker
    it **carries the text it shows** — 8 characters for "13:30:02" — so
    reading the runs of a paragraph gives a run that looks like any other,
    and rewriting it destroys the field and leaves its text behind. Measured:
    a flat rewrite of a paragraph holding three fields left none;
  * `getPresentation(False)` is what it shows, `getPresentation(True)` the
    command Writer names it by ("Date", "Page number", "Statistics");
  * the service to create one is `com.sun.star.text.TextField.<kind>` with a
    capital T. The lower-case `textfield` spelling answers for some kinds and
    then throws from `editeng` when inserted, because it hands back the
    drawing layer's field rather than Writer's;
  * a comment is a text field too, so anything walking `getTextFields()` has
    to leave the annotations out or every note in the margin turns up as a
    field.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import (ANNOTATION_SERVICE, AddressError,
                        DEFAULT_FIELD_REPORTS, _get_property, _supports,
                        _text_payload, refusal)

logger = logging.getLogger(__name__)

# The kinds a caller can ask for, in words rather than service names. Each is
# (service, properties to set) — measured to insert and show something.
FIELD_KINDS = {
    "date": ("DateTime", {"IsDate": True, "IsFixed": False}),
    "time": ("DateTime", {"IsDate": False, "IsFixed": False}),
    "page_number": ("PageNumber", {"NumberingType": 4}),
    "page_count": ("PageCount", {"NumberingType": 4}),
    "title": ("DocInfo.Title", {}),
    "subject": ("DocInfo.Subject", {}),
    "author": ("Author", {}),
    "file_name": ("FileName", {}),
}

# Which kind a field found in a document is, by the service it supports.
KIND_OF_SERVICE = {
    "com.sun.star.text.TextField.DateTime": "date or time",
    "com.sun.star.text.TextField.PageNumber": "page_number",
    "com.sun.star.text.TextField.PageCount": "page_count",
    "com.sun.star.text.TextField.DocInfo.Title": "title",
    "com.sun.star.text.TextField.DocInfo.Subject": "subject",
    "com.sun.star.text.TextField.Author": "author",
    "com.sun.star.text.TextField.FileName": "file_name",
}


class FieldsMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _text_fields(self, doc: Any) -> List[Any]:
        """Every field in the document except the comments"""
        return list(self._each_text_field(doc))

    def _each_text_field(self, doc: Any):
        """The fields of a document one at a time, the comments left out

        A comment is a text field as well, so this filter is the difference
        between listing the fields and listing the margin. It yields rather
        than collects because the enumeration *is* the cost: 1536 fields of a
        real guide are 0.5–1.0s of bare `nextElement` over a socket, and a
        caller asking for a window of them should not pay for the rest.
        """
        try:
            fields = doc.getTextFields().createEnumeration()
        except Exception as e:
            logger.error(f"Could not enumerate the fields: {e}")
            return
        while True:
            try:
                if not fields.hasMoreElements():
                    return
                field = fields.nextElement()
            except Exception as e:
                logger.info(f"The fields would not go on: {e}")
                return
            if _supports(field, ANNOTATION_SERVICE):
                continue
            yield field

    def _fields_with_addresses(self, doc: Any) -> List[tuple]:
        """[(field, described)] for the whole document, in one walk of it.

        The offset comes from walking the paragraph's portions, not from
        measuring the anchor: a cursor of zero width standing *at* a field's
        position still hands back the text the field shows — measured, a date
        at the start of a paragraph reported itself as seven characters in —
        so any arithmetic on string lengths is wrong around a field. The
        portions are in step with the string, which is what
        `_position_in` already relies on.
        """
        placed, position = {}, {}
        body = doc.getText()
        index = 0
        try:
            paragraphs = body.createEnumeration()
            while paragraphs.hasMoreElements():
                paragraph = paragraphs.nextElement()
                if not hasattr(paragraph, "createEnumeration"):
                    continue
                offset = 0
                portions = paragraph.createEnumeration()
                while portions.hasMoreElements():
                    portion = portions.nextElement()
                    shown = portion.getString()
                    if _get_property(portion, "TextPortionType", "Text") \
                            == "TextField":
                        field = _get_property(portion, "TextField", None)
                        if field is not None:
                            placed[self._field_key(field)] = {
                                "paragraph": index, "offset": offset,
                                "length": len(shown)}
                            position[self._field_key(field)] = shown
                    offset += len(shown)
                index += 1
        except Exception as e:
            logger.info(f"Could not walk the document for its fields: {e}")

        found = []
        for field in self._text_fields(doc):
            key = self._field_key(field)
            described = self._describe_field(doc, field,
                                             address=placed.get(key),
                                             shown=position.get(key))
            found.append((field, described))
        return found

    def _field_key(self, field: Any) -> str:
        """What tells one field from another across pyuno proxies.

        A proxy is minted fresh per call, so the objects cannot be compared;
        what a field *is* plus what it shows is enough to pair the walk with
        the enumeration, and two identical fields in one place are
        indistinguishable to a reader as well.
        """
        return (f"{self._field_presentation(field, True)}\x00"
                f"{self._field_presentation(field, False)}")

    def _describe_field(self, doc: Any, field: Any, address: Any = "unplaced",
                        shown: Optional[str] = None) -> Dict[str, Any]:
        """A field as a caller sees it: what it is, what it says, where."""
        services = []
        try:
            services = list(field.getSupportedServiceNames())
        except Exception as e:
            logger.info(f"A field would not name itself: {e}")
        kind = next((KIND_OF_SERVICE[one] for one in services
                     if one in KIND_OF_SERVICE), None)
        if kind == "date or time":
            kind = "date" if _get_property(field, "IsDate", False) else "time"
        described = {
            "kind": kind,
            "command": self._field_presentation(field, True),
            "text": self._field_presentation(field, False),
            "fixed": bool(_get_property(field, "IsFixed", False)),
            "service": next((one for one in services
                             if one.startswith("com.sun.star.text.TextField.")),
                            None),
        }
        if address == "unplaced":
            try:
                address, _, _ = self._locate_range(doc, field.getAnchor())
            except Exception as e:
                logger.info(f"Could not place a field: {e}")
                address = None
        described["address"] = address
        described["anchor_text"] = shown if shown is not None else described["text"]
        return described

    def _field_presentation(self, field: Any, command: bool) -> Optional[str]:
        try:
            return field.getPresentation(command)
        except Exception as e:
            logger.info(f"A field would not say what it shows: {e}")
            return None

    def list_fields(self, address: Any = None, start: int = 0,
                    count: Optional[int] = None, number: bool = False,
                    doc: Any = None) -> Dict[str, Any]:
        """
        The fields of a document, with what each one shows and where it sits

        Scoped like the comments — the whole document, a section, a
        paragraph, a range or the selection. Comments are fields too and are
        left out; list_comments is for those.

        A field's **offset** can only come from walking the portions of every
        paragraph: a zero-width cursor standing at a field hands back the
        text the field shows, so string arithmetic around one lies. That walk
        is what `number: true` pays for — 14 seconds for the 1536 fields of a
        real guide.

        Two things kept a scoped call paying for the whole document. It asked
        the document for **every** field and then threw away the ones outside
        the scope — 2.26s to answer that a paragraph holds none — where a
        field is a portion of its own paragraph (type `TextField`, carrying
        the field), so the scope can read its own paragraphs instead. And
        every field reported is held by an anchor, 1.79s for 1536 of them and
        1536 of the 2000 anchors the store keeps, so the answer is paged:
        `count` reports a window (200 by default, and a default is not a
        ceiling) and `more` says another call is worth making.

        The enumeration **is** the cost of a call about the whole document —
        1536 fields are 0.5 to 1.0s of bare `nextElement` over a socket, and
        `getTextFields()` has no count to ask for — so it stops one field
        past the window. `total` therefore comes back null unless the walk
        reached the end, which a `count` past the last field does.
        """
        doc, error = self._writer_document(doc, "Listing fields")
        if error:
            return error

        try:
            covers, scope = (self._comment_scope(doc, address) if number
                             else self._scope_over(doc, address))
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        window = max(1, int(DEFAULT_FIELD_REPORTS if count is None else count))
        begin = max(0, int(start or 0))

        if number:
            placed = [described for _field, described
                      in self._fields_with_addresses(doc)
                      if covers(described["address"])]
            total = len(placed)
            fields = placed[begin:begin + window]
            kinds = sorted({one["kind"] for one in placed if one["kind"]})
            more = begin + len(fields) < total
        else:
            kept = []
            exhausted = True
            source = (self._each_text_field(doc) if address is None
                      else iter(self._fields_over(doc, address)))
            for field in source:
                if address is not None:
                    try:
                        if not covers(field.getAnchor()):
                            continue
                    except Exception as e:
                        logger.info(f"A field would not say where it is: {e}")
                        continue
                if len(kept) > begin + window:
                    # One past the window is all `more` needs to know, and
                    # the rest of the document is not walked for it: the
                    # 1536 fields of a real guide are 0.5–1.0s of bare
                    # enumeration, which is the whole cost of this call.
                    exhausted = False
                    break
                kept.append(field)
            fields = []
            for field in kept[begin:begin + window]:
                try:
                    anchor = field.getAnchor()
                except Exception as e:
                    logger.info(f"A field would not say where it is: {e}")
                    continue
                held = self._anchor_handle(self._hold_anchor(doc, anchor),
                                           "text")
                fields.append(self._describe_field(
                    doc, field, address={"anchor": held}))
            # What the answer knows: the total only when the walk reached the
            # end of the document, and the kinds of the fields it reports.
            total = len(kept) if exhausted else None
            kinds = sorted({one["kind"] for one in fields if one["kind"]})
            more = (not exhausted) or begin + len(fields) < len(kept)

        return {"success": True, "fields": fields, "count": len(fields),
                "total": total,
                "start": begin,
                "more": more,
                "kinds": kinds,
                "order": "reading" if number
                         else "as the document names them",
                "scope": scope}

    def _kind_of(self, field: Any) -> Optional[str]:
        """What kind of field this is, for one UNO call"""
        try:
            services = list(field.getSupportedServiceNames())
        except Exception as e:
            logger.info(f"A field would not name itself: {e}")
            return None
        kind = next((KIND_OF_SERVICE[one] for one in services
                     if one in KIND_OF_SERVICE), None)
        if kind == "date or time":
            kind = "date" if _get_property(field, "IsDate", False) else "time"
        return kind

    def _fields_over(self, doc: Any, address: Any) -> List[Any]:
        """The fields in the paragraphs a scope covers, from their portions

        A field is a portion of type `TextField` carrying the field itself —
        measured — so a paragraph names its own fields, and a scoped listing
        costs the scope rather than every field the document has. A comment
        is a text field too and is left out here as everywhere.
        """
        paragraphs, _, _ = self._paragraphs_over(doc, address)
        if paragraphs is None:
            return self._text_fields(doc)
        found = []
        for paragraph in paragraphs:
            try:
                portions = paragraph.createEnumeration()
            except Exception as e:
                logger.info(f"Could not read a paragraph's portions: {e}")
                continue
            while portions.hasMoreElements():
                portion = portions.nextElement()
                if _get_property(portion, "TextPortionType",
                                 "Text") != "TextField":
                    continue
                field = _get_property(portion, "TextField", None)
                if field is None or _supports(field, ANNOTATION_SERVICE):
                    continue
                found.append(field)
        return found

    def insert_field(self, address: Any, kind: str, fixed: bool = False,
                     track_changes: Optional[bool] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Put a field where an address points, so the document writes it itself

        A field takes the place of what it is inserted over only when the
        address covers text; at a caret it goes in without taking anything
        away.
        """
        doc, error = self._writer_document(doc, "Inserting a field")
        if error:
            return error
        if kind not in FIELD_KINDS:
            return refusal(
                "INVALID_PARAMETER",
                f"kind is one of {', '.join(sorted(FIELD_KINDS))}, got "
                f"{kind!r}")

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        service, settings = FIELD_KINDS[kind]

        def edit():
            field = doc.createInstance(f"com.sun.star.text.TextField.{service}")
            for name, value in settings.items():
                try:
                    setattr(field, name, value)
                except Exception as e:
                    logger.info(f"Could not set {name} on a {kind} field: {e}")
            if kind == "page_number":
                try:
                    from com.sun.star.text.PageNumberType import CURRENT
                    field.SubType = CURRENT
                except Exception as e:
                    logger.info(f"Could not ask for the current page: {e}")
            if "IsFixed" in settings:
                try:
                    field.IsFixed = bool(fixed)
                except Exception as e:
                    logger.info(f"Could not fix a field: {e}")
            owner = target.getText()
            owner.insertTextContent(target, field, bool(target.getString()))
            described = self._describe_field(doc, field)
            return {"kind": kind, "shows": described["text"],
                    "command": described["command"],
                    "address": described["address"], "fixed": bool(fixed)}

        return self._guarded_edit(doc, f"MCP: insert a {kind} field",
                                  track_changes, edit)

    def update_fields(self, doc: Any = None) -> Dict[str, Any]:
        """Make every field redraw itself, as Writer does on F9"""
        doc, error = self._writer_document(doc, "Updating fields")
        if error:
            return error

        before = [self._field_presentation(field, False)
                  for field in self._text_fields(doc)]

        def edit():
            doc.getTextFields().refresh()
            after = [self._field_presentation(field, False)
                     for field in self._text_fields(doc)]
            return {"fields": len(after),
                    "changed": sum(1 for was, now in zip(before, after)
                                   if was != now)}

        return self._guarded_edit(doc, "MCP: update fields", None, edit)

    def delete_field(self, address: Any, doc: Any = None) -> Dict[str, Any]:
        """
        Take a field away, leaving the text around it

        The field is named by where it sits, since a field has no id of its
        own: an address covering exactly one field is removed, and an address
        covering several is refused with their addresses, so a caller can
        name the one it means.
        """
        doc, error = self._writer_document(doc, "Deleting a field")
        if error:
            return error

        try:
            covers, _scope = self._comment_scope(doc, address)
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)
        # Kept beside their descriptions, so the one to remove is the object
        # itself rather than something found again by comparing addresses.
        held = [(field, described) for field, described
                in self._fields_with_addresses(doc)
                if covers(described["address"])]
        found = [described for _field, described in held]
        if not found:
            return refusal("NOT_FOUND",
                           "there is no field there; list_fields says where "
                           "the fields of this document are")
        if len(found) > 1:
            # An address covering several is not always a caller's mistake:
            # a field that shows nothing — a title the document has not got —
            # is an empty range that every neighbouring address overlaps. So
            # an address that matches one of them *exactly* names that one.
            # Compared against what the address *resolves to*, not against
            # the way it was written: an anchor names the same stretch as a
            # paragraph and an offset, and could never match one as a dict.
            try:
                wanted, _, _ = self._locate_range(
                    doc, self._resolve_address(doc, address))
            except Exception as e:
                logger.info(f"Could not place the address given: {e}")
                wanted = address if isinstance(address, dict) else {}

            def same_place(one):
                return all(one.get(key) == wanted.get(key)
                           for key in ("paragraph", "offset", "length",
                                       "table", "cell"))

            exact = [(field, one) for field, one in held
                     if same_place(one["address"] or {})]
            if len(exact) != 1:
                return refusal(
                    "INVALID_PARAMETER",
                    f"that covers {len(found)} fields; name one of them by "
                    f"its own address, as list_fields reports it",
                    fields=[one["address"] for one in found])
            held = exact

        target, wanted = held[0]

        def edit():
            anchor = target.getAnchor()
            anchor.getText().removeTextContent(target)
            return {"deleted": wanted["kind"], "was_showing": wanted["text"],
                    "address": wanted["address"]}

        return self._guarded_edit(doc, "MCP: delete a field", None, edit)
