"""Comments: the notes in the margin, and what they are anchored to.

A comment is not text but a pair of empty marker portions, so reading runs
once skipped them and rewriting destroyed them. A comment is named by its
annotation's Name, the language of its text comes from the "Comment"
paragraph style when the note is created, and rewriting the text under one
takes the comment with it.
"""

from functools import cmp_to_key
from typing import Any, Optional, Dict, List
import uuid
import logging
from uno_values import (ANNOTATION_SERVICE, AddressError, COMMENT_STYLE, 
    _comment_language, _describe_comment, _get_property, _heading_level, 
    _locale, _locale_name, _stamp_comment, _supports, _text_payload, 
    _write_comment_text, refusal)

logger = logging.getLogger(__name__)


class CommentsMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _anchor_comment(self, doc: Any, span: Any, comment: Dict[str, Any]):
        """
        Put a comment on a range, keeping its author and text

        Gives it a Name if it has none. Writer names the comments made in its
        own interface, but one created through the API comes back with an
        empty Name — measured — and a comment with no name cannot be picked
        out for editing or deleting later.
        """
        note = doc.createInstance(ANNOTATION_SERVICE)
        note.Author = str(comment.get("author", "") or "")
        note.Content = str(comment.get("content", "") or "")
        _stamp_comment(note)
        if comment.get("resolved"):
            try:
                note.Resolved = True
            except Exception as e:
                logger.info(f"Could not mark a comment resolved: {e}")
        # An annotation refuses the object getSelection() hands back — and a
        # cursor made *from* it — with "no SwTextAttr inserted?", while the
        # same stretch works when the cursor is walked from its start to its
        # end. Measured; so every anchor is rebuilt that way.
        owner = span.getText()
        try:
            walked = owner.createTextCursorByRange(span.getStart())
            walked.gotoRange(span.getEnd(), True)
        except Exception as e:
            logger.info(f"Could not rebuild the anchor, using it as it is: {e}")
            walked = span
        owner.insertTextContent(walked, note, True)
        parent = comment.get("reply_to")
        if parent:
            # A reply is an annotation like any other, joined to its parent by
            # ParentName — measured, and it survives saving as
            # loext:parent-name. Set after insertion, which is when the note
            # belongs to a document.
            try:
                note.ParentName = str(parent)
            except Exception as e:
                logger.info(f"Could not make a comment a reply: {e}")
        if not (_get_property(note, "Name", "") or ""):
            try:
                note.Name = f"__Annotation__mcp_{uuid.uuid4().hex[:16]}"
            except Exception as e:
                logger.info(f"Could not name a comment: {e}")
        return note

    def _section_bounds(self, doc: Any, index: Any) -> tuple:
        """
        (first, last) body paragraph of the section a heading opens

        A section runs from its heading to the paragraph before the next
        heading of the same or a higher level, which is what a reader means
        by "this section" — the sub-sections under it included.
        """
        if not isinstance(index, int) or isinstance(index, bool):
            raise AddressError(f"heading must be a paragraph index, "
                               f"got {index!r}")

        levels = []
        enumeration = doc.getText().createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            levels.append(_heading_level(element))

        if index < 0 or index >= len(levels):
            raise AddressError(f"no body paragraph {index}, so no heading there")
        level = levels[index]
        if level <= 0:
            raise AddressError(f"paragraph {index} is not a heading, so it "
                               f"opens no section — take a heading's paragraph "
                               f"index from get_outline")

        last = len(levels) - 1
        for position in range(index + 1, len(levels)):
            if 0 < levels[position] <= level:
                last = position - 1
                break
        return index, last

    def _scope_over(self, doc: Any, address: Any,
                    unknown: bool = False) -> tuple:
        """(predicate on a *range*, description of the scope)

        The scope of `_comment_scope` is a predicate on an address — on
        paragraph **numbers** — and working one out for every item of a list
        is a sweep of the body: two UNO calls for each of 6981 paragraphs,
        which is why listing 195 bookmarks took 20 seconds. A range can be
        compared with the scope directly, four calls apiece and no numbers,
        which is what a listing needs when it reports anchors.
        """
        if address is None:
            return (lambda item: True), {"document": True}
        if not isinstance(address, dict):
            raise AddressError(f"address must be an object, got {address!r}")

        body = doc.getText()
        if "heading" in address:
            first, last = self._section_bounds(doc, address["heading"])
            span = self._resolve_address(doc, {"paragraph": first,
                                               "through": last})
            described = {"heading": address["heading"],
                         "paragraphs": [first, last]}
        else:
            span = self._resolve_address(doc, address)
            described = dict(address)

        def covers(item: Any) -> bool:
            try:
                return self._covers(body, span, item, unknown=unknown)
            except Exception:
                # A range in a table cell cannot be compared with the body,
                # and a scope of body paragraphs does not hold it anyway.
                return unknown

        return covers, described

    def _comment_scope(self, doc: Any, address: Any) -> tuple:
        """
        (predicate on a comment's address, description of the scope)

        Comments can be asked for by document, by section, by paragraph, by an
        exact range or by what is selected. A comment matches a range when its
        anchor overlaps it; a point anchor matches when it sits inside.
        """
        if address is None:
            return (lambda located: True), {"document": True}
        if not isinstance(address, dict):
            raise AddressError(f"address must be an object, got {address!r}")

        if "heading" in address:
            first, last = self._section_bounds(doc, address["heading"])
            def in_section(located):
                return located is not None and located.get("paragraph") \
                    is not None and first <= located["paragraph"] <= last
            return in_section, {"heading": address["heading"],
                                "paragraphs": [first, last]}

        asks_for_a_range = (address.get("selection")
                            or address.get("offset") is not None
                            or address.get("length") is not None)
        if asks_for_a_range:
            located, _, _ = self._locate_range(
                doc, self._resolve_address(doc, address))
            if located.get("paragraph") is None:
                raise AddressError("that address is outside the body text")
            paragraph = located["paragraph"]
            start = located["offset"]
            end = start + located["length"]
            if end == start and address.get("selection"):
                # Nothing selected, only a caret: the paragraph is what the
                # caller can have meant.
                return (lambda l: l is not None
                        and l.get("paragraph") == paragraph), \
                    {"paragraph": paragraph}

            def overlaps(l):
                if l is None or l.get("paragraph") != paragraph:
                    return False
                if l["length"] == 0:
                    return start <= l["offset"] <= end
                return l["offset"] < end and l["offset"] + l["length"] > start
            return overlaps, {"paragraph": paragraph, "offset": start,
                              "length": end - start}

        if address.get("through") is not None:
            # A block of whole paragraphs means all of them, here as
            # everywhere else an address is read.
            first = self._paragraph_index_of(
                doc, {"paragraph": address.get("paragraph")})
            last = self._paragraph_index_of(
                doc, {"paragraph": address["through"]})
            if last < first:
                first, last = last, first

            def in_block(located):
                return (located is not None
                        and located.get("paragraph") is not None
                        and first <= located["paragraph"] <= last)
            return in_block, {"paragraphs": [first, last]}

        index = self._paragraph_index_of(doc, address)
        return (lambda l: l is not None and l.get("paragraph") == index), \
            {"paragraph": index}

    def _find_comment(self, doc: Any, comment_id: Any) -> Any:
        """The annotation whose Name is comment_id, or None"""
        if not isinstance(comment_id, str) or not comment_id:
            raise AddressError("comment_id must be the id of a comment, as "
                               "list_comments reports it")
        fields = doc.getTextFields().createEnumeration()
        while fields.hasMoreElements():
            field = fields.nextElement()
            if not _supports(field, ANNOTATION_SERVICE):
                continue
            if (_get_property(field, "Name", "") or "") == comment_id:
                return field
        return None

    def update_comment(self, comment_id: str, text: Optional[str] = None,
                       author: Optional[str] = None,
                       resolved: Optional[bool] = None,
                       language: Optional[str] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Change a comment's text, author, language or resolved state

        The text the comment is anchored to is untouched: this edits the note
        in the margin, not the document.

        Text, author and resolved are changed in place, so the comment keeps
        its id and its date. A `language` cannot be: Writer marks a note when
        the note is created, so the comment is made again on the same anchor —
        with a new id and today's date — and the language of the document's
        comments is set along with it, since one comment cannot have its own.
        The result says both.
        """
        doc, error = self._writer_document(doc, "Editing a comment")
        if error:
            return error

        if text is None and author is None and resolved is None \
                and language is None:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": "Nothing to change: pass text, author, language "
                             "or resolved"}
        if language is not None:
            try:
                _locale(language)
                self._comment_style(doc)
            except AddressError as e:
                return refusal("INVALID_ADDRESS", e)
        if text is not None and (not isinstance(text, str) or not text):
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": "text must be a non-empty string; to remove a "
                             "comment use delete_comment"}

        try:
            note = self._find_comment(doc, comment_id)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        if note is None:
            return {"success": False, "code": "NOT_FOUND",
                    "error": f"No comment with id {comment_id} in this "
                             f"document. Take an id from list_comments."}

        was = _describe_comment(note)
        anchor_address = None
        if language is not None:
            try:
                located, _, _ = self._locate_range(doc, note.getAnchor())
                anchor_address = located
            except Exception as e:
                logger.info(f"Could not locate a comment's anchor: {e}")
            if anchor_address is None or anchor_address.get("paragraph") is None:
                return {"success": False, "code": "UNSUPPORTED",
                        "error": "This comment's anchor is outside the body "
                                 "text, so it cannot be made again in another "
                                 "language"}

        def edit():
            changed = []
            remade = None

            if language is not None:
                # Only a new note can carry a language, so make this one
                # again on the same anchor.
                wanted = {"author": author if author is not None
                          else was["author"],
                          "content": text if text is not None else was["content"],
                          "resolved": was["resolved"] if resolved is None
                          else bool(resolved),
                          "reply_to": was["reply_to"]}
                previous_language = self._set_comment_style_language(doc,
                                                                     language)
                # The replies are noted before the parent goes: the new note
                # has a new id, and a reply left naming the old one would
                # hang in the margin pointing at nothing.
                hanging = self._replies_to(doc, was["id"])
                note.getAnchor().getText().removeTextContent(note)
                span = self._resolve_address(doc, anchor_address)
                remade = self._anchor_comment(doc, span, wanted)
                repointed = []
                for one in hanging:
                    if (_get_property(one, "ParentName", "") or "") != was["id"]:
                        continue                  # a reply to a reply
                    try:
                        one.ParentName = _get_property(remade, "Name", "") or ""
                        repointed.append(_get_property(one, "Name", "") or "")
                    except Exception as e:
                        logger.info(f"Could not re-point a reply: {e}")
                changed.append("language")
                if text is not None:
                    changed.append("text")
                if author is not None:
                    changed.append("author")
                if resolved is not None:
                    changed.append("resolved")
                described = _describe_comment(remade)
                return {"id": described["id"], "previous_id": was["id"],
                        "recreated": True, "changed": changed,
                        "reply_to": described["reply_to"],
                        "replies_repointed": repointed,
                        "author": described["author"],
                        "content": described["content"],
                        "resolved": described["resolved"],
                        "language": described["language"],
                        "comment_language_set": {
                            "language": language, "was": previous_language,
                            "scope": "the document's comments: a language "
                                     "cannot be given to one comment alone"}}

            if text is not None:
                _write_comment_text(note, text)
                changed.append("text")
            if author is not None:
                note.Author = str(author)
                changed.append("author")
            if resolved is not None:
                note.Resolved = bool(resolved)
                changed.append("resolved")
            described = _describe_comment(note)
            return {"id": described["id"], "changed": changed,
                    "recreated": False,
                    "author": described["author"],
                    "content": described["content"],
                    "resolved": described["resolved"],
                    "language": described["language"]}

        return self._guarded_edit(doc, "MCP: edit comment", None, edit)

    def _replies_to(self, doc: Any, comment_id: str) -> List[Any]:
        """Every annotation hanging off this one, replies to replies included.

        Measured: removing a comment that has replies leaves them behind,
        still naming a parent that is no longer in the document — Writer says
        nothing and the margin shows orphans. So whoever deletes has to know
        what hangs off what.
        """
        by_parent = {}
        for note in self._annotations(doc):
            parent = _get_property(note, "ParentName", "") or ""
            if parent:
                by_parent.setdefault(parent, []).append(note)
        found = []
        pending = [comment_id]
        while pending:
            for note in by_parent.get(pending.pop(), []):
                found.append(note)
                pending.append(_get_property(note, "Name", "") or "")
        return found

    def delete_comment(self, comment_id: Optional[str] = None,
                       author: Optional[str] = None, address: Any = None,
                       all: bool = False, with_replies: bool = False,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Remove comments, leaving the text they were anchored to

        One by id, or everything an author left, or a part of the document,
        or the lot — one way only, as the review tools pick their changes.
        Returns what was deleted, so an assistant can say what it removed and
        the text can be commented again if that was a mistake.

        A parent whose replies would be left behind is refused unless
        `with_replies` says to take the thread: deleting it alone leaves them
        in the margin pointing at nothing. When the replies are in the set
        already — "all", say — there is nothing to refuse.
        """
        doc, error = self._writer_document(doc, "Deleting comments")
        if error:
            return error

        picked, refused = self._comments_picked(doc, comment_id, author,
                                                address, all)
        if refused:
            return refused
        if not picked:
            return {"success": True, "deleted": 0,
                    "note": "nothing matched, so nothing was deleted"}

        going = {(_get_property(note, "Name", "") or "") for note in picked}
        orphans = []
        for note in picked:
            for reply in self._replies_to(doc,
                                          _get_property(note, "Name", "") or ""):
                if (_get_property(reply, "Name", "") or "") not in going:
                    orphans.append(reply)
        if orphans and not with_replies:
            return refusal(
                "INVALID_PARAMETER",
                f"that would leave {len(orphans)} repl"
                f"{'ies' if len(orphans) > 1 else 'y'} in the margin pointing "
                f"at a comment that has gone; pass with_replies=true to "
                f"remove the whole thread",
                replies=[_get_property(one, "Name", "") or ""
                         for one in orphans])

        described = [_describe_comment(note) for note in picked]
        anchors = []
        for note in picked:
            try:
                anchors.append(
                    _text_payload(note.getAnchor().getString())["text"])
            except Exception as e:
                logger.info(f"Could not read a comment's anchor: {e}")
                anchors.append(None)

        # Named before anything goes: an annotation that has been removed no
        # longer answers with its own Name.
        going_out = [(note, _get_property(note, "Name", "") or "")
                     for note in orphans + picked]

        def edit():
            removed = []
            # Replies first, and a thread from the leaves inward, so nothing
            # is left naming a parent that has gone.
            for one, marked in reversed(going_out):
                try:
                    one.getAnchor().getText().removeTextContent(one)
                    removed.append(marked)
                except Exception as e:
                    logger.info(f"Could not remove a comment: {e}")
            gone = [dict(one, anchor_text=where)
                    for one, where in zip(described, anchors)]
            answer = {"deleted": len(removed), "comments": gone,
                      "replies_deleted": [one for one in removed
                                          if one not in
                                          {c["id"] for c in described}]}
            if len(gone) == 1:
                # What a caller that named one comment used to get back.
                answer.update({"id": gone[0]["id"],
                               "author": gone[0]["author"],
                               "content": gone[0]["content"],
                               "anchor_text": gone[0]["anchor_text"]})
            return answer

        return self._guarded_edit(doc, "MCP: delete comments", None, edit)

    def _comment_style(self, doc: Any) -> Any:
        """The "Comment" paragraph style, which notes take their language from"""
        family = doc.StyleFamilies.getByName("ParagraphStyles")
        if not family.hasByName(COMMENT_STYLE):
            raise AddressError(f'this document has no "{COMMENT_STYLE}" '
                               f'paragraph style, so the language of its '
                               f'comments cannot be set')
        return family.getByName(COMMENT_STYLE)

    def _set_comment_style_language(self, doc: Any, language: str) -> Any:
        """
        Set the language the document's comments are written in

        It has to stay set: a note whose style is put back afterwards reports
        the language it was put back to, so there is no way to give one
        comment a language of its own.
        """
        style = self._comment_style(doc)
        was = _locale_name(_get_property(style, "CharLocale", None))
        style.CharLocale = _locale(language)
        return was

    def set_comment_language(self, language: str,
                             doc: Any = None) -> Dict[str, Any]:
        """
        Set the language the document's comments are written in

        Writer spell checks a note in the margin against the language of the
        note's own text, which it takes from the "Comment" paragraph style
        when the note is created. Setting the style therefore marks the
        comments added from now on and leaves the ones already there as they
        are — update_comment changes one of those, by making it again.
        """
        doc, error = self._writer_document(doc, "Setting the comment language")
        if error:
            return error

        try:
            locale = _locale(language)
            style = self._comment_style(doc)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        except Exception as e:
            logger.error(f"Could not reach the comment style: {e}")
            return refusal("FAILED", e)

        was = _locale_name(_get_property(style, "CharLocale", None))

        def edit():
            style.CharLocale = locale
            return {"language": _locale_name(_get_property(style, "CharLocale",
                                                           None)),
                    "was": was,
                    "comments_already_there": len(self._annotations(doc)),
                    "scope": "the comments added from now on; the ones "
                             "already in the document keep the language they "
                             "were written in, and update_comment can change "
                             "one of those"}

        return self._guarded_edit(doc, "MCP: set comment language", None, edit)

    def _addresses_in_order(self, doc: Any, ranges: List[Any]) -> List[Any]:
        """An address for every range, in one walk of the body.

        The sweep that places search hits wants them in document order, so
        the ranges are sorted by `compareRegionStarts` first — the only
        comparison that works across pyuno proxies. A range it cannot compare
        (one inside a table cell throws) keeps its place and falls to the slow
        path inside the sweep, which knows cell addresses.
        """
        if not ranges:
            return []
        body = doc.getText()

        def before(one, other):
            try:
                return body.compareRegionStarts(one, other)
            except Exception:
                return 0            # not comparable: leave them as they are

        order = list(range(len(ranges)))
        try:
            order.sort(key=cmp_to_key(
                lambda left, right: -before(ranges[left], ranges[right])))
        except Exception as e:
            logger.info(f"Could not put the anchors in order: {e}")
        located = [None] * len(ranges)
        for position, placed in zip(
                order, self._locate_matches(doc, [ranges[i] for i in order])):
            located[position] = (placed or {}).get("address")
        return located

    def _annotations(self, doc: Any) -> List[Any]:
        """Every comment field in the document"""
        found = []
        try:
            fields = doc.getTextFields().createEnumeration()
        except Exception as e:
            logger.error(f"Could not enumerate comments: {e}")
            return found
        while fields.hasMoreElements():
            field = fields.nextElement()
            if _supports(field, ANNOTATION_SERVICE):
                found.append(field)
        return found

    def list_comments(self, address: Any = None,
                      author: Optional[str] = None,
                      resolved: Optional[bool] = None,
                      number: bool = False,
                      doc: Any = None) -> Dict[str, Any]:
        """
        The comments of a document, a section, a paragraph, a range or the
        selection

        Each carries the address of the text it is anchored to and that text
        itself, so a caller can see what a comment is about without reading
        the whole document, plus the id that names it for editing.

        **A scope reads its own paragraphs.** Asking a document for its
        comments means walking every text field it has — 0.672s for the 1536
        fields of a 519-page guide, which holds no comments at all — and the
        scope was then a predicate on paragraph *numbers*, so an address had
        to be numbered first: another walk, 1.75s to answer "none here".
        A comment is a marker portion of the paragraph it sits in
        (`Annotation` … `AnnotationEnd`, measured), so a scoped call now reads
        the portions of the paragraphs its scope covers and costs what the
        scope costs. Only a call about the **whole** document walks the
        fields, and only `number: true` walks the body for the numbers.
        """
        doc, error = self._writer_document(doc, "Listing comments")
        if error:
            return error

        try:
            covers, scope = (self._comment_scope(doc, address) if number
                             else self._scope_over(doc, address))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        try:
            notes, anchors = (self._comments_of_document(doc)
                              if address is None or number
                              else self._comments_over(doc, address))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        except Exception as e:
            logger.error(f"Could not enumerate comments: {e}")
            return refusal("FAILED", e)

        # Numbering them is one sweep of the body — where addressing each on
        # its own would be a walk apiece, and a document with seven of them
        # spent 1.3s in here. An anchor is two UNO calls and says the same
        # thing to every tool.
        placed = (self._addresses_in_order(doc, anchors) if number
                  else [None] * len(anchors))

        comments = []
        for field, anchor, located in zip(notes, anchors, placed):
            described = _describe_comment(field)
            try:
                text = anchor.getString()
            except Exception as e:
                logger.info(f"Could not read a comment's anchor: {e}")
                text = None
            if number:
                described["address"] = located
                if not covers(located):
                    continue
            else:
                if not covers(anchor):
                    continue
                described["address"] = {"anchor": self._anchor_handle(
                    self._hold_anchor(doc, anchor, known=text), "text")}
            described["anchor_text"] = (None if text is None
                                        else _text_payload(text)["text"])
            if author is not None and described["author"] != author:
                continue
            if resolved is not None and bool(described["resolved"]) != bool(resolved):
                continue
            comments.append(described)

        # A comment in a table cell has no body paragraph, so it sorts after
        # the ones that do, by table and cell.
        def where(comment):
            address = comment["address"] or {}
            paragraph = address.get("paragraph")
            return (10 ** 9 if paragraph is None else paragraph,
                    address.get("table") or "",
                    address.get("cell") or "",
                    address.get("offset") or 0)

        if number:
            comments.sort(key=where)
        # A reply is joined to its parent by ParentName and sits on the same
        # anchor, so a thread arrives as several comments over one stretch.
        # Saying which replies hang off which comment saves the caller
        # rebuilding it, and `threads` counts what a reader would call
        # conversations rather than notes.
        children = {}
        for comment in comments:
            if comment.get("reply_to"):
                children.setdefault(comment["reply_to"], []).append(comment["id"])
        for comment in comments:
            comment["replies"] = children.get(comment["id"], [])
        return {"success": True, "comments": comments, "count": len(comments),
                "threads": sum(1 for one in comments if not one.get("reply_to")),
                "replies": sum(1 for one in comments if one.get("reply_to")),
                "unresolved": sum(1 for one in comments
                                  if not one.get("resolved")),
                "authors": sorted({one["author"] for one in comments
                                   if one["author"]}),
                "order": "reading" if number
                         else "as the document names them",
                "scope": scope}

    def _comments_of_document(self, doc: Any) -> tuple:
        """Every comment in the document, from its text fields

        A comment is a text field, so this is the only way to ask a document
        for all of them — and it costs every field the document has, 0.672s
        for the 1536 of a real guide. A scoped call has a cheaper way in.
        """
        notes, anchors = [], []
        fields = doc.getTextFields().createEnumeration()
        while fields.hasMoreElements():
            field = fields.nextElement()
            if not _supports(field, ANNOTATION_SERVICE):
                continue
            try:
                anchors.append(field.getAnchor())
                notes.append(field)
            except Exception as e:
                logger.info(f"A comment would not say where it is: {e}")
        return notes, anchors

    def _comments_over(self, doc: Any, address: Any) -> tuple:
        """The comments in the paragraphs a scope covers, from their portions

        Writer marks a comment with empty portions of the paragraph it sits in
        — `Annotation` where it opens and `AnnotationEnd` where it closes, or
        a lone `Annotation` for a point anchor — so a paragraph names its own
        comments, and the walk is the scope's rather than the document's.
        """
        paragraphs, _, _ = self._paragraphs_over(doc, address)
        if paragraphs is None:
            return self._comments_of_document(doc)
        notes, anchors, seen = [], [], set()
        for paragraph in paragraphs:
            try:
                portions = paragraph.createEnumeration()
            except Exception as e:
                logger.info(f"Could not read a paragraph's portions: {e}")
                continue
            while portions.hasMoreElements():
                portion = portions.nextElement()
                if _get_property(portion, "TextPortionType",
                                 "Text") != "Annotation":
                    continue
                note = _get_property(portion, "TextField", None)
                if note is None:
                    continue
                try:
                    name = note.Name
                except Exception:
                    name = None
                if name and name in seen:
                    continue
                if name:
                    seen.add(name)
                try:
                    anchors.append(note.getAnchor())
                    notes.append(note)
                except Exception as e:
                    logger.info(f"A comment would not say where it is: {e}")
        return notes, anchors

    def add_comment(self, address: Any = None, text: str = "",
                    author: str = "", language: Optional[str] = None,
                    reply_to: Optional[str] = None,
                    doc: Any = None) -> Dict[str, Any]:
        """
        Anchor a new comment to the text at an address, or reply to one

        `language` marks the note's own text, which is what Writer spell
        checks: a Russian note left at the document's language is underlined
        word by word in the margin. It cannot be given to one comment alone,
        so passing it also sets the language of the document's comments —
        which the result says.

        `reply_to` makes the new comment a reply: it goes on its parent's own
        anchor, so a thread sits over one stretch of text the way Writer's
        own replies do, and needs no address of its own.
        """
        doc, error = self._writer_document(doc, "Adding a comment")
        if error:
            return error

        if not isinstance(text, str) or not text:
            return {"success": False, "code": "INVALID_PARAMETER", "error": "text must be a non-empty string"}

        parent = None
        if reply_to is not None:
            if address is not None:
                return refusal("INVALID_PARAMETER",
                               "a reply goes on its parent's own anchor, so "
                               "it takes no address of its own")
            try:
                parent = self._find_comment(doc, reply_to)
            except AddressError as e:
                return refusal("INVALID_PARAMETER", e)
            if parent is None:
                return refusal("NOT_FOUND",
                               f"no comment with id {reply_to} in this "
                               f"document; take an id from list_comments")
        elif address is None:
            return refusal("INVALID_PARAMETER",
                           "say where the comment goes: an address, or "
                           "reply_to for a reply to another comment")

        try:
            target = (parent.getAnchor() if parent is not None
                      else self._resolve_address(doc, address))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        if language is not None:
            try:
                _locale(language)          # refuse a bad tag before editing
                self._comment_style(doc)
            except AddressError as e:
                return refusal("INVALID_ADDRESS", e)

        def edit():
            was = None
            if language is not None:
                was = self._set_comment_style_language(doc, language)
            note = self._anchor_comment(doc, target,
                                        {"author": author, "content": text,
                                         "reply_to": reply_to})
            result = {"id": _get_property(note, "Name", "") or "",
                      "anchor_text": _text_payload(target.getString())["text"],
                      "author": author, "content": text,
                      "language": _comment_language(note),
                      "reply_to": reply_to}
            if language is not None:
                result["comment_language_set"] = {
                    "language": language, "was": was,
                    "scope": "the document's comments: a language cannot be "
                             "given to one comment alone"}
            return result

        return self._guarded_edit(doc, "MCP: add comment", None, edit)

    # -- several comments at once ------------------------------------------

    def _comments_picked(self, doc: Any, comment_id: Optional[str],
                         author: Optional[str], address: Any,
                         everything: bool) -> tuple:
        """(annotations, refusal) for the one way a caller named them."""
        named = [one for one in (comment_id, author, address)
                 if one is not None]
        if len(named) + (1 if everything else 0) != 1:
            return None, refusal(
                "INVALID_PARAMETER",
                "say which comments, and only one way: comment_id for one of "
                "them, author for everyone's by that name, address for a part "
                "of the document, or all=true for every comment in it")

        if comment_id is not None:
            try:
                note = self._find_comment(doc, comment_id)
            except AddressError as e:
                return None, refusal("INVALID_PARAMETER", e)
            if note is None:
                return None, refusal(
                    "NOT_FOUND",
                    f"No comment with id {comment_id} in this document. Take "
                    f"an id from list_comments.")
            return [note], None

        listed = self.list_comments(address=address, author=author, doc=doc)
        if not listed.get("success"):
            return None, listed
        wanted = {one["id"] for one in listed["comments"]}
        return [note for note in self._annotations(doc)
                if (_get_property(note, "Name", "") or "") in wanted], None

    def resolve_comments(self, comment_id: Optional[str] = None,
                         author: Optional[str] = None, address: Any = None,
                         all: bool = False, resolved: bool = True,
                         doc: Any = None) -> Dict[str, Any]:
        """
        Mark comments resolved, or reopen them

        Measured: `Resolved` belongs to each annotation on its own — marking
        a parent leaves its replies unresolved, so Writer shows half a
        settled thread. The replies of anything picked here follow it, and
        the result says how many did.
        """
        doc, error = self._writer_document(doc, "Resolving comments")
        if error:
            return error

        picked, refused = self._comments_picked(doc, comment_id, author,
                                                address, all)
        if refused:
            return refused
        if not picked:
            return {"success": True, "marked": 0, "resolved": bool(resolved),
                    "note": "nothing matched, so nothing was marked"}

        chosen = {(_get_property(note, "Name", "") or "") for note in picked}
        followers = []
        for note in picked:
            for reply in self._replies_to(doc,
                                          _get_property(note, "Name", "") or ""):
                if (_get_property(reply, "Name", "") or "") not in chosen:
                    followers.append(reply)
                    chosen.add(_get_property(reply, "Name", "") or "")

        def edit():
            marked = []
            for note in picked + followers:
                try:
                    note.Resolved = bool(resolved)
                    marked.append(_get_property(note, "Name", "") or "")
                except Exception as e:
                    logger.info(f"Could not mark a comment: {e}")
            return {"marked": len(marked), "comments": marked,
                    "replies_followed": len(followers),
                    "resolved": bool(resolved)}

        return self._guarded_edit(doc, "MCP: resolve comments", None, edit)
