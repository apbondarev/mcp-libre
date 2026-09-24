"""Reading a document: its paragraphs, its headings, what it says where.

Every hit and every paragraph carries an address that can be handed straight
back to a tool that edits it.
"""

from typing import Any, Dict, List, Optional
import logging
from uno_values import (AddressError, DEFAULT_PARAGRAPH_COUNT,
    DEFAULT_SEARCH_RESULTS, MAX_ANCHORS,
    DEFAULT_OUTLINE_ENTRIES, MAX_PARAGRAPH_COUNT, MAX_SEARCH_RESULTS, 
    CELL_SERVICE, MAX_TEXT_CHARS, WRITER_SERVICE, _get_property,
    _heading_level, _level_from_style_name, _level_source, _supports,
    _text_payload, refusal)

logger = logging.getLogger(__name__)

# How much of a hit's neighbourhood one call will carry.
MAX_NEIGHBOUR_PARAGRAPHS = 50


class ReadingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def get_text_content(self, doc: Any = None) -> Dict[str, Any]:
        """Get text content from a document"""
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"success": False, "code": "NO_DOCUMENT", "error": "No document available"}
            
            if _supports(doc, WRITER_SERVICE):
                text = doc.getText().getString()
                return {"success": True, "content": text, "length": len(text)}
            else:
                return {"success": False, "code": "WRONG_DOCUMENT_TYPE", "error": f"Text extraction not supported for {self._get_document_type(doc)}"}
                
        except Exception as e:
            logger.error(f"Failed to get text content: {e}")
            return refusal("FAILED", e)

    def _window_from(self, doc: Any, address: Any) -> tuple:
        """(paragraphs of the body, where this address is among them).

        A read that starts at an address used to walk the body **twice**:
        once to work the number out and once to reach it. The sweep is one
        walk of two UNO calls a paragraph, the place inside it is found by
        halving, and the window is read from the sweep itself.
        """
        sweep = self._body_sweep(doc)
        target = self._resolve_address(doc, address)
        # The start, not the whole range: a block covers several paragraphs
        # and none of them holds it.
        index = self._paragraph_holding(doc.getText(), sweep,
                                        target.getStart())
        if index is None:
            raise AddressError("that address is outside the body text — a "
                               "table cell, most likely — so it names no "
                               "body paragraph")
        return sweep, index

    def _paragraphs_from(self, doc: Any, address: Any) -> tuple:
        """(first paragraph, how many it covers) for a read that starts at an
        address rather than at a number.

        The reading tools hand out addresses precisely so a caller need never
        carry a number from one call to the next; the tool that pages through
        a document has to take one back, or the habit breaks at the first
        page. A block says its own length, which is what "read this section"
        means.
        """
        first = self._paragraph_index_of(doc, address)
        through = address.get("through")
        if isinstance(through, int) and not isinstance(through, bool):
            last = self._paragraph_index_of(doc, {"paragraph": through})
            return min(first, last), abs(last - first) + 1
        return first, None

    def read_paragraphs(self, start: Any = 0,
                        count: Optional[int] = None,
                        anchors: bool = True,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Read a window of body paragraphs with their indices and styles

        `count` is a window, not a limit: 50 when nobody says, and a caller
        that asks for a whole document gets it. `total_paragraphs` always
        reflects the whole document, so the caller can page through it.
        Anchors are the one bound — a read of more paragraphs than the
        session can hold anchors for is refused rather than answered with
        tokens that were let go before the answer was built.

        `start` is a number **or an address**: an anchor, a paragraph, or a
        block — `{"anchor": "a7f3c1"}` and the `address` a previous read
        handed back are both accepted, so a long document can be walked
        without a number ever being carried from one call to the next. A
        block (`through`) also says how many to read when `count` does not.
        The `start` in the result is the number the address came to.

        Every paragraph comes with an `address` holding a paragraph anchor
        beside its number, so passing that address back reaches the same
        paragraph after the numbers have moved — by the caller's own edits or
        the reader's typing. It is on unless `anchors` is false: a plan made
        from numbers alone went wrong on a real document the moment the
        reader pressed Enter.

        A formula is an object, not text, so a paragraph's string passes over
        it ("equals  of the whole"). A paragraph that holds one also carries
        `formulas` — name, StarMath text and offset — and `text_with_formulas`,
        its string with each put back where it stands. `text` itself is left
        as it is: every offset in every address counts in it.
        """
        try:
            doc, error = self._writer_document(doc, "Reading paragraphs")
            if error:
                return error

            asked = count
            sweep = None
            if isinstance(start, dict):
                try:
                    through = start.get("through")
                    sweep, start = self._window_from(doc, start)
                    if asked is None and isinstance(through, int) \
                            and not isinstance(through, bool):
                        last = self._paragraph_index_of(doc,
                                                        {"paragraph": through})
                        asked = abs(last - start) + 1
                        start = min(start, last)
                except AddressError as e:
                    return refusal("INVALID_ADDRESS", e)
            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"start must be a non-negative integer or an "
                                 f"address, got {start!r}"}

            window = max(1, int(DEFAULT_PARAGRAPH_COUNT if asked is None
                                else asked))
            if anchors and window > MAX_ANCHORS:
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"a read of {window} paragraphs would hold "
                                 f"more anchors than the {MAX_ANCHORS} this "
                                 f"session keeps, and the earliest would be "
                                 f"let go before the answer was built: ask "
                                 f"for at most {MAX_ANCHORS} at a time, or "
                                 f"pass anchors: false to read further in "
                                 f"one go"}
            paragraphs = []
            total = 0
            # A formula is not text, so a paragraph holding one has to say
            # so — and it says so itself: a formula is a Frame portion of the
            # paragraph, measured. Placing every formula of the document
            # instead was a sweep of the body on every call (12s to read one
            # paragraph of a guide holding a single formula), and scanning
            # the document's embedded objects was a fixed cost that made one
            # paragraph dearer than fifty.
            standing: Dict[int, List[Dict[str, Any]]] = {}

            # The sweep, when an address was resolved against it, is the
            # body already walked: reading the window from it saves the
            # second walk that reaching a number costs.
            walked = iter(sweep) if sweep is not None else None
            enumeration = (None if walked is not None
                           else doc.getText().createEnumeration())
            while True:
                if walked is not None:
                    element = next(walked, None)
                    if element is None:
                        break
                else:
                    if not enumeration.hasMoreElements():
                        break
                    element = enumeration.nextElement()
                    if not hasattr(element, "getStart"):
                        continue
                if start <= total < start + window:
                    raw = element.getString()
                    entry = _text_payload(raw)
                    entry["paragraph"] = total
                    here = self._formulas_in_paragraph(element)
                    if here:
                        standing[total] = here
                    if total in standing:
                        entry["formulas"] = [
                            {"name": one["name"], "formula": one["formula"],
                             "offset": one["offset"]}
                            for one in standing[total]]
                        entry["text_with_formulas"] = _text_payload(
                            self._with_formulas(raw, standing[total]))["text"]
                    entry["style"] = _get_property(element, "ParaStyleName")
                    if anchors:
                        # The paragraph itself, held with a cursor at its
                        # start — see uno_anchors for why both.
                        token = self._hold_paragraph_anchor(doc, element, total)
                        entry["address"] = {
                            "paragraph": total,
                            "anchor": self._anchor_handle(token, "paragraph")
                        } if token else {"paragraph": total}
                    else:
                        entry["address"] = {"paragraph": total}
                    paragraphs.append(entry)
                total += 1

            return {
                "success": True,
                "paragraphs": paragraphs,
                "start": start,
                "count": len(paragraphs),
                "anchors": bool(anchors),
                "total_paragraphs": total
            }

        except Exception as e:
            logger.error(f"Failed to read paragraphs: {e}")
            return refusal("FAILED", e)

    def get_outline(self, start: Any = 0, count: Optional[int] = None,
                    anchors: bool = True, number: bool = False,
                    matching: Optional[str] = None,
                    doc: Any = None) -> Dict[str, Any]:
        """
        List the document's headings — the structure Writer itself keeps

        Gives an assistant a map of a long document without reading it, and
        every entry doubles as an address to read or edit from.

        **Writer knows its own structure and is never asked twice.** This used
        to enumerate every paragraph of the document and ask each whether it
        was a heading: 2.5s on a 519-page guide, of which 2.1s was the bare
        enumeration — nothing in the tool's own logic could make that cheaper.
        Writer's search finds the paragraphs of a style in milliseconds
        instead, so the styles that carry an outline level (or are named
        "Heading N") are searched for and the hits merged into document order:
        0.41s for the same 938 headings, and the walk is kept for the calls
        that need it.

        **A level from a style's name is a guess, and it says so.** Measured
        on that guide: its chapter numbering gives level 1 to "Title" and 2 to
        "Heading 1", while "Heading 2", "Heading 3" and "Heading 4" carry no
        outline level at all — so Writer's Navigator shows 256 entries where
        this tool reported 938, and called a "Heading 2" paragraph level 2,
        the very level of the "Heading 1" paragraphs above it. Each entry now
        says in `level_from` whether its level is Writer's (`outline level`)
        or the name's (`style name`), and `outline_entries` reports how many
        entries Writer itself holds — `doc.getLinks()`'s "Headings", the
        Navigator's own list, which answers in a millisecond.

        That count is also the check: when it disagrees with the number of
        entries the styles account for, somebody has given a paragraph an
        outline level by hand, the search cannot see it, and the walk is made
        instead. `found_by` says which happened. Asking for `number` or
        turning `anchors` off also walks, since an entry must be addressable
        and a number is only free to a walk.

        Long documents page the way `read_paragraphs` pages: `start` is a
        place — a number or an address — and `more` says another call is worth
        making, with the last heading's own address to hand back as `start`.

        `matching` asks for the headings whose text holds a phrase, which is
        how a chapter is found without reading the map: the stream stops at
        the first page of matches, and only they are anchored. Measured on the
        519-page guide — the whole map is 3.2s and paging through it to
        "Chapter 12" no cheaper, since each page searches and anchors again,
        while `matching` reaches it in about a second and holds one anchor.
        """
        try:
            doc, error = self._writer_document(doc, "An outline")
            if error:
                return error

            if not isinstance(start, dict):
                if not isinstance(start, int) or isinstance(start, bool) \
                        or start < 0:
                    return {"success": False, "code": "INVALID_PARAMETER",
                            "error": f"start must be a non-negative integer "
                                     f"or an address, got {start!r}"}
            # A default, not a ceiling: ask for more and you get more, since
            # a map of a document is what this answers and half a map is no
            # map. 200 is only what an unasked-for window holds.
            window = max(1, int(DEFAULT_OUTLINE_ENTRIES if count is None
                                else count))

            said = self._writer_outline_count(doc)
            plan = None
            if not number and anchors:
                plan = self._outline_by_styles(doc, said)
            found_by = "styles"
            total_paragraphs = None
            if plan is None:
                found_by = "walk"
                entries, total_paragraphs = self._outline_by_walk(doc)
                stream, total = iter(entries), len(entries)
            else:
                stream, total = plan

            try:
                reached = self._outline_reaches(doc, start, found_by)
            except AddressError as e:
                return refusal("INVALID_ADDRESS", e)

            wanted = (" ".join(str(matching).split()).lower()
                      if matching else None)

            def holds_it(one):
                if wanted is None:
                    return True
                try:
                    text = one["range"].getString() if "range" in one \
                        else one["element"].getString()
                except Exception:
                    return False
                one["text"] = text
                return wanted in " ".join(text.split()).lower()

            # Only as far as the window: every heading passed over costs a
            # fetch and a comparison, and every one taken costs its anchor.
            before = 0
            shown = []
            after = 0
            for one in stream:
                if not holds_it(one):
                    continue
                if not shown and not reached(one):
                    before += 1
                    continue
                if len(shown) < window:
                    shown.append(one)
                    continue
                after = 1          # one more is all "more" needs to know
                break

            headings = [self._outline_entry(doc, one, anchors)
                        for one in shown]
            if after:
                logger.info("Outline paged, there are headings after this window")

            return {
                "success": True,
                "headings": headings,
                "start": start if isinstance(start, int) else None,
                "count": len(headings),
                "headings_before": before,
                "total_headings": total,
                "total_paragraphs": total_paragraphs,
                "found_by": found_by,
                "matching": matching,
                # What Writer itself counts as structure, in a millisecond.
                "outline_entries": said,
                "more": after > 0,
                # What this key meant before paging existed: there are
                # headings this answer does not carry.
                "truncated": after > 0
            }

        except Exception as e:
            logger.error(f"Failed to get outline: {e}")
            return refusal("FAILED", e)

    def _outline_by_walk(self, doc: Any) -> tuple:
        """(entries, paragraphs counted) — every paragraph asked in turn

        Complete, and the price of it is one UNO call per paragraph and then
        some: 2.5s on a 6981-paragraph document over a socket. It sees a level
        set by hand, which no search can.
        """
        entries = []
        total = 0
        enumeration = doc.getText().createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            level = _heading_level(element)
            if level > 0:
                entries.append({"paragraph": total, "level": level,
                                "level_from": _level_source(element),
                                "element": element, "range": element})
            total += 1
        return entries, total

    def _outline_by_styles(self, doc: Any, said: Optional[int]) -> Optional[tuple]:
        """(headings in document order, how many there are), or None

        Milliseconds against seconds — but a style search can only find what a
        style accounts for, so it is checked against Writer's own count before
        it is trusted. The headings come back as a **stream**: a caller asking
        for five of 938 paid for all of them otherwise, and every part of this
        was proportional to the document rather than to the answer — 0.25s
        fetching every hit, 0.34s asking each whether it sat in a table cell,
        0.21s merging them all, for five entries that cost 8ms.
        """
        styles = self._outline_styles(doc)
        if not styles:
            return None
        sources = []
        total = 0
        counted = 0
        for name in sorted(styles):
            level, source = styles[name]
            try:
                found = self._search_style(doc, name)
                many = found.getCount()
            except Exception as e:
                logger.info(f"Could not search for the style {name}: {e}")
                continue
            if not many:
                continue
            total += many
            if source == "outline level":
                counted += many
            sources.append({"found": found, "count": many, "taken": 0,
                            "level": level, "level_from": source})
        if said is not None and said != counted:
            logger.info(f"Writer counts {said} outline entries where the "
                        f"styles account for {counted}: walking instead")
            return None
        return self._headings_in_order(doc.getText(), sources), total

    def _outline_styles(self, doc: Any) -> Dict[str, tuple]:
        """{style name: (level, where the level came from)} for the headings

        Reading the level of every paragraph style is 143 property reads on a
        real document — 0.037s — and it answers both questions at once: which
        styles Writer counts as structure, and at which level.
        """
        wanted = {}
        try:
            family = doc.StyleFamilies.getByName("ParagraphStyles")
            names = list(family.getElementNames())
        except Exception as e:
            logger.info(f"Could not read the paragraph styles: {e}")
            return wanted
        for name in names:
            try:
                level = family.getByName(name).OutlineLevel
            except Exception:
                level = 0
            if isinstance(level, int) and not isinstance(level, bool) \
                    and level > 0:
                wanted[name] = (level, "outline level")
                continue
            named = _level_from_style_name(name)
            if named:
                wanted[name] = (named, "style name")
        return wanted

    def _writer_outline_count(self, doc: Any) -> Optional[int]:
        """How many entries Writer's own outline holds, or None if it will not say

        `doc.getLinks()` is the Navigator's list of link targets — "Tables",
        "Sections", "Headings" and the rest — and Writer keeps it up to date
        itself: 256 headings of a 519-page guide came back in **0.8 ms**, and
        renaming a heading showed in the next call. Each entry carries only
        its display name, so it is a count and a list of texts, not a set of
        addresses; that is why it checks the search rather than replacing it.
        """
        try:
            return len(doc.getLinks().getByName("Headings").getElementNames())
        except Exception as e:
            logger.info(f"Writer would not say how many headings it has: {e}")
            return None

    def _headings_in_order(self, body: Any, sources: List[Dict]):
        """Yield the hits of several style searches in document order

        Each search answers in document order already, so this is a merge:
        every step compares the heads — one UNO call apiece, a handful of
        styles — and fetches a hit only when it is reached. A hit inside a
        **table cell** is passed over here, where it costs nothing, since an
        address counts body paragraphs and the walk this stands in for never
        saw inside a cell either.
        """
        heads = [None] * len(sources)

        def head(which):
            if heads[which] is not None:
                return heads[which]
            source = sources[which]
            while source["taken"] < source["count"]:
                one = source["found"].getByIndex(source["taken"])
                source["taken"] += 1
                try:
                    if _supports(one.getText(), CELL_SERVICE):
                        continue
                except Exception:
                    continue
                heads[which] = {"range": one, "level": source["level"],
                                "level_from": source["level_from"],
                                "paragraph": None}
                return heads[which]
            return None

        while True:
            best = -1
            for which in range(len(sources)):
                if head(which) is None:
                    continue
                if best < 0:
                    best = which
                    continue
                try:
                    if body.compareRegionStarts(heads[which]["range"],
                                                heads[best]["range"]) == 1:
                        best = which
                except Exception as e:
                    logger.info(f"Could not order two headings: {e}")
            if best < 0:
                return
            yield heads[best]
            heads[best] = None

    def _outline_reaches(self, doc: Any, start: Any, found_by: str):
        """A test for "this heading is where the caller asked to begin"

        The walk knows every heading's number, so it compares numbers; the
        search knows only places, so it compares those — `start` resolved
        once, each heading against it until one is at or past it.
        """
        if not isinstance(start, dict) and not start:
            return lambda one: True
        if found_by == "walk":
            first = (self._paragraphs_from(doc, start)[0]
                     if isinstance(start, dict) else start)
            return lambda one: one["paragraph"] >= first
        where = self._resolve_address(
            doc, {"paragraph": start} if isinstance(start, int) else start)
        body = doc.getText()

        def at_or_after(one):
            try:
                return body.compareRegionStarts(where, one["range"]) >= 0
            except Exception as e:
                logger.info(f"Could not place the start of an outline: {e}")
                return False
        return at_or_after

    def _outline_entry(self, doc: Any, one: Dict[str, Any],
                       anchors: bool) -> Dict[str, Any]:
        """One heading, as a caller sees it: its level, its text, its address"""
        element = one.get("element")
        if element is None:
            element = self._paragraph_from(one["range"])
        entry = {
            "paragraph": one["paragraph"],
            "level": one["level"],
            # Whether Writer calls this structure or the style's name does.
            "level_from": one["level_from"],
            "text": (one.get("text") or one["range"].getString())[:MAX_TEXT_CHARS]
        }
        token = (self._hold_paragraph_anchor(doc, element, one["paragraph"])
                 if anchors and element is not None else None)
        address = ({"paragraph": one["paragraph"]}
                   if one["paragraph"] is not None else {})
        if token:
            address["anchor"] = self._anchor_handle(token, "paragraph")
        entry["address"] = address
        return entry

    def _match_in(self, body: Any, paragraph: Any, index: int, match: Any,
                  start: Any) -> Dict[str, Any]:
        """One match, addressed inside the paragraph that holds it

        The offset is measured backwards, from the match's start to the start
        of its paragraph, the way _locate_range measures it. Walking forwards
        instead — a cursor at the paragraph start told to reach the match —
        overshoots a field: a date came back at the offset of the field after
        it, and deleting it was refused as running past the paragraph.
        """
        cursor = body.createTextCursorByRange(start)
        cursor.gotoStartOfParagraph(True)
        payload = _text_payload(paragraph.getString())
        return {"address": {"paragraph": index,
                            "offset": len(cursor.getString()),
                            "length": len(match.getString())},
                "matched": match.getString(),
                "context": payload["text"],
                "context_truncated": payload["truncated"]}

    def _locate_matches(self, doc: Any, matches: List[Any]) -> List[Dict[str, Any]]:
        """
        Address every match, in one walk of the body

        Asking each match separately walks the document from the start again:
        measured at 0.85s per hit on a 500-paragraph document over a socket,
        so twenty hits took ten seconds — slow enough to send a caller off to
        write its own script. findAll returns matches in document order, so
        one sweep can hand each of them its paragraph as it passes.
        """
        body = doc.getText()
        located = [None] * len(matches)
        if not matches:
            return located

        # Every call crosses the bridge, so the sweep spends as few as it can:
        # each match's start once, and per paragraph one getStart and one
        # comparison. A match belongs to the last paragraph that began before
        # it, which is known as soon as the next paragraph begins after it.
        try:
            starts = [match.getStart() for match in matches]
        except Exception as e:
            logger.info(f"Could not take the starts of the matches: {e}")
            starts = [None] * len(matches)

        pointer = 0
        index = -1
        previous = None
        previous_index = None

        enumeration = body.createEnumeration()
        while enumeration.hasMoreElements() and pointer < len(matches):
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            index += 1
            start = element.getStart()

            while pointer < len(matches) and previous is not None:
                if starts[pointer] is None:
                    break
                try:
                    if body.compareRegionStarts(starts[pointer], start) != 1:
                        break          # the match is not before this paragraph
                except Exception:
                    # A match in a table cell is not comparable with the body
                    # at all; it is left for the slow path, which knows how to
                    # address a cell. Stalling here would stop the sweep.
                    pointer += 1
                    continue
                try:
                    located[pointer] = self._match_in(body, previous,
                                                      previous_index,
                                                      matches[pointer],
                                                      starts[pointer])
                except Exception as e:
                    # The comparison did not refuse it, but the cursor does:
                    # measured on a hit inside a cell, which compares against
                    # the body without complaint and then will not be reached
                    # from it.
                    logger.info(f"A match was left for the slow path: {e}")
                pointer += 1

            previous, previous_index = element, index

        # The last paragraph has nothing after it to mark its end.
        while pointer < len(matches) and previous is not None:
            if starts[pointer] is None:
                break
            try:
                located[pointer] = self._match_in(body, previous, previous_index,
                                                  matches[pointer],
                                                  starts[pointer])
            except Exception as e:
                logger.info(f"A match was left for the slow path: {e}")
            pointer += 1

        # Whatever the sweep could not place — matches inside table cells —
        # is asked the slow way, since each has an address of its own.
        for position, match in enumerate(matches):
            if located[position] is not None:
                continue
            address, paragraph_cursor, _ = self._locate_range(doc, match)
            context = _text_payload(paragraph_cursor.getString())
            located[position] = {"address": address,
                                 "matched": match.getString(),
                                 "context": context["text"],
                                 "context_truncated": context["truncated"]}
        return located

    def _paragraphs_by_index(self, doc: Any,
                             wanted: Any) -> Dict[int, Dict[str, Any]]:
        """
        The paragraphs at these indices, in one walk of the body

        Asking for them one at a time walks the document from the start each
        time, which for twenty hits with ten neighbours apiece is two hundred
        walks of a five-hundred-paragraph document.
        """
        asked = {index for index in wanted if isinstance(index, int)
                 and index >= 0}
        found = {}
        if not asked:
            return found
        position = 0
        enumeration = doc.getText().createEnumeration()
        while enumeration.hasMoreElements() and len(found) < len(asked):
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            if position in asked:
                payload = _text_payload(element.getString())
                found[position] = {
                    "paragraph": position,
                    "text": payload["text"],
                    "truncated": payload["truncated"],
                    "style": _get_property(element, "ParaStyleName", "") or ""}
            position += 1
        return found

    def find_text(self, query: str, regex: bool = False,
                  case_sensitive: bool = False,
                  max_results: int = DEFAULT_SEARCH_RESULTS,
                  paragraphs_before: int = 0, paragraphs_after: int = 0,
                  anchors: bool = True,
                  doc: Any = None) -> Dict[str, Any]:
        """
        Find text in the active Writer document

        Each hit carries an address that resolves back to the match, so a hit
        can be handed straight to a tool that edits it, plus the containing
        paragraph as context. total_hits is the real number of matches even
        when the list is capped.

        `paragraphs_before` and `paragraphs_after` bring the neighbourhood of
        each hit along — index, style and text — which is what a caller does
        next anyway: a heading like "Operation" is only interesting together
        with the code block under it, and fetching that separately is a
        second call per hit.

        Every hit's `address` carries an anchor that goes on pointing at the
        match while the document changes around it — a plan made from one
        search survives its own edits and the reader's typing, where the
        paragraph numbers alone do not. It is on unless `anchors` is false.
        """
        try:
            doc, error = self._writer_document(doc, "Searching")
            if error:
                return error

            if not isinstance(query, str) or not query:
                return {"success": False, "code": "INVALID_PARAMETER", "error": "query must be a non-empty string"}

            limit = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
            for label, value in (("paragraphs_before", paragraphs_before),
                                 ("paragraphs_after", paragraphs_after)):
                if not isinstance(value, int) or isinstance(value, bool) \
                        or value < 0 or value > MAX_NEIGHBOUR_PARAGRAPHS:
                    return {"success": False, "code": "INVALID_PARAMETER",
                            "error": f"{label} must be between 0 and "
                                     f"{MAX_NEIGHBOUR_PARAGRAPHS}, got "
                                     f"{value!r}"}

            descriptor = doc.createSearchDescriptor()
            descriptor.SearchString = query
            descriptor.SearchRegularExpression = bool(regex)
            descriptor.SearchCaseSensitive = bool(case_sensitive)

            found = doc.findAll(descriptor)
            total = found.getCount()

            matches = [found.getByIndex(position)
                       for position in range(min(total, limit))]
            hits = self._locate_matches(doc, matches)

            if anchors:
                for hit, match in zip(hits, matches):
                    # The sweep that placed the hits knows each one's
                    # paragraph, so the anchor is told: without it, reading
                    # through a hit's own address walked the body to work the
                    # number out again — 5.4s at paragraph 2725.
                    token = self._hold_anchor(
                        doc, match,
                        index=(hit.get("address") or {}).get("paragraph"))
                    if token and isinstance(hit.get("address"), dict):
                        # Handed out inside the address and nowhere else, so
                        # passing the address back is all it takes to use it.
                        hit["address"] = dict(
                            hit["address"],
                            anchor=self._anchor_handle(token, "text"))

            if paragraphs_before or paragraphs_after:
                wanted = set()
                for hit in hits:
                    index = (hit["address"] or {}).get("paragraph")
                    if index is None:
                        continue          # a hit in a cell has no neighbours
                    wanted.update(range(max(0, index - paragraphs_before),
                                        index + paragraphs_after + 1))
                paragraphs = self._paragraphs_by_index(doc, wanted)
                for hit in hits:
                    index = (hit["address"] or {}).get("paragraph")
                    if index is None:
                        hit["before"] = None
                        hit["after"] = None
                        continue
                    hit["before"] = [paragraphs[number] for number
                                     in range(max(0, index - paragraphs_before),
                                              index)
                                     if number in paragraphs]
                    hit["after"] = [paragraphs[number] for number
                                    in range(index + 1,
                                             index + paragraphs_after + 1)
                                    if number in paragraphs]

            logger.info(f"Found {total} matches for {query!r}, returning {len(hits)}")
            return {
                "success": True,
                "hits": hits,
                "total_hits": total,
                "truncated": total > len(hits)
            }

        except Exception as e:
            logger.error(f"Failed to search: {e}")
            return refusal("FAILED", e)
