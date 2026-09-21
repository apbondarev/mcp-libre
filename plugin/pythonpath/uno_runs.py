"""Runs: the formatted pieces a stretch of text is made of.

setString over a range holding several runs flattens it to one, so a
translation has to read the runs, change their text and write them back with
each one's look restored. What a rewrite would cost — links, comments,
pictures, styles — is counted before anything is written.
"""

from typing import Any, Optional, Dict, List
import logging
from uno_values import (MAX_RUN_PARAGRAPHS, REDLINE_KINDS, TABLE_SERVICE,
    AddressError, _supports,
    _colour_name,
    _comment_key, _distinct_changes, 
    _describe_comment, _distinct_comments, _distinct_images, _get_property, 
    _is_italic, _locale, _locale_name, _same_paragraph, _text_payload, refusal)

logger = logging.getLogger(__name__)


class RunsMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def read_runs(self, address: Any, doc: Any = None) -> Dict[str, Any]:
        """
        The formatted runs the text at an address is made of

        Needed because replacing a mixed-formatting range flattens it: one
        setString over four runs leaves one run, so a monospace term loses its
        font and a coloured phrase loses its colour. With the runs read out,
        text can be translated piece by piece and written back through
        replace_runs with each piece's look restored.

        Every run carries an address that resolves to exactly that run.
        """
        doc, error = self._writer_document(doc, "Reading runs")
        if error:
            return error

        try:
            target = self._resolve_address(doc, address)
            located, paragraph_cursor, _ = self._locate_range(
                doc, target, self._paragraph_hint(address, doc))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        index = located["paragraph"]
        if index is None and not located.get("cell"):
            return {"success": False, "code": "INVALID_ADDRESS",
                    "error": "That address is outside the body text and in no "
                             "table cell, so its runs cannot be read"}

        # A block address covers whole paragraphs, and the runs of all of them
        # are what a caller asking for a block means. Reporting only the
        # first paragraph's while saying the range spans fifteen reads as a
        # promise not kept — and it cost a caller a call per paragraph to
        # find out.
        block = self._block_of(address, located)
        held = None
        passed = None
        try:
            if block:
                runs, read, stopped, passed = self._runs_across(doc, block)
            else:
                paragraph = self._paragraph_of(doc, located, paragraph_cursor)
                runs = self._runs_in(doc, located, paragraph_cursor,
                                     paragraph=paragraph)
                read, stopped = [index, index] if index is not None else None, \
                    False
                if paragraph is not None and index is not None \
                        and located.get("offset", 0) == 0 \
                        and located.get("length") == len(paragraph.getString()):
                    # The whole paragraph was read, so the address to write
                    # it back through is the paragraph itself, held.
                    held = self._hold_paragraph_anchor(doc, paragraph, index)
        except Exception as e:
            logger.error(f"Could not read the runs: {e}")
            return refusal("FAILED", e)

        result = {"success": True, "runs": runs, "count": len(runs),
                  "paragraph": index}
        self._say_which_formulas(doc, result, index, read, located, block,
                                 paragraph_cursor, target)
        if held:
            result["address"] = {"paragraph": index, "anchor": held}
        if block:
            result["paragraphs"] = read
            result["paragraphs_read"] = (read[1] - read[0] + 1) if read else 0
            if stopped:
                result["truncated"] = True
                result["truncated_at"] = MAX_RUN_PARAGRAPHS
        # Runs belong to one paragraph. When the range reaches further — into
        # a table, say — saying so beats letting a caller believe the runs
        # are the whole of what was asked for.
        try:
            return self._note_what_is_out_of_reach(doc, target, result,
                                                   known_paragraph=index,
                                                   known_block=block,
                                                   known_tables=passed)
        except Exception as e:
            logger.info(f"Could not say what the range reaches: {e}")
            return result

    def _say_which_formulas(self, doc: Any, result: Dict[str, Any],
                            index: Optional[int], read: Any,
                            located: Dict[str, Any], block: Any,
                            paragraph_cursor: Any = None,
                            span: Any = None) -> None:
        """Add `formulas` when the paragraphs read hold any.

        The runs are made of text, and a formula is not text, so between two
        runs there can be a formula that none of them mentions.

        One paragraph is answered from its own anchor comparisons
        (`_formulas_in`), a block from the block's range (`_formulas_across`).
        Neither places every formula in the document, which walks the body.
        """
        if index is None:
            return
        try:
            lower = located.get("offset", 0)
            upper = lower + (located.get("length") or 0)
            if not block:
                here = self._formulas_in(doc, index, lower, upper,
                                         paragraph_cursor)
            else:
                first, last = read or [index, index]
                here = self._formulas_across(doc, first, last, span)
        except Exception as e:
            logger.info(f"Could not look for formulas among the runs: {e}")
            return
        if here:
            result["formulas"] = here

    def _block_of(self, address: Any, located: Dict[str, Any]):
        """(first, last) when an address names a block of paragraphs, else None

        Only `through` makes a block here: a range with an offset and a
        length is one paragraph's, and a cell address is the cell's.
        """
        if not isinstance(address, dict) or address.get("through") is None:
            return None
        if located.get("cell") or located.get("paragraph") is None:
            return None
        first = located["paragraph"]
        last = address["through"]
        if not isinstance(last, int) or isinstance(last, bool):
            return None
        if last < first:
            first, last = last, first
        return first, last

    def _runs_across(self, doc: Any, block) -> tuple:
        """The runs of every paragraph in a block, and the tables in between

        One walk for the whole block: reaching a paragraph by index is a walk
        of its own, and doing that per paragraph made a block of four cost
        four walks of a three-hundred-paragraph document. The tables are
        collected by the same walk, which passes them anyway — asking
        `_table_positions` afterwards was a second walk of the *whole*
        document to find what had just gone by.
        """
        first, last = block
        runs: List[Dict[str, Any]] = []
        body = doc.getText()
        read_last = first
        stopped = False
        tables: List[tuple] = []
        index = 0
        enumeration = body.createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                # A table stands *between* paragraphs, so one with paragraphs
                # of the block on either side is inside it.
                if first < index <= last and _supports(element, TABLE_SERVICE):
                    tables.append((element, index))
                continue
            if index < first:
                index += 1
                continue
            if index > last or index - first >= MAX_RUN_PARAGRAPHS:
                stopped = index <= last
                break
            located = {"paragraph": index, "offset": 0,
                       "length": len(element.getString())}
            cursor = body.createTextCursorByRange(element.getStart())
            cursor.gotoEndOfParagraph(True)
            runs.extend(self._runs_in(doc, located, cursor,
                                      paragraph=element))
            read_last = index
            index += 1
        return runs, [first, read_last], stopped, tables

    def _runs_in(self, doc: Any, located: Dict[str, Any],
                 paragraph_cursor: Any,
                 paragraph: Any = None) -> List[Dict[str, Any]]:
        """The runs a located range covers, clipped to it

        `paragraph` is taken from a caller that already holds it: finding one
        by index walks the body, and a block of paragraphs read one walk at a
        time cost a second and a half where the whole block should cost what
        one paragraph does.
        """
        index = located["paragraph"]
        span_start = located["offset"]
        span_end = span_start + max(located["length"], 0)
        if span_end == span_start:
            span_end = span_start + len(paragraph_cursor.getString())

        if paragraph is None:
            paragraph = self._paragraph_of(doc, located, paragraph_cursor)
        if paragraph is None:
            return []
        shift = 0
        if located.get("cell"):
            # Offsets in a cell address count from the cell's start, while a
            # paragraph's runs count from its own; shift them into step.
            before = 0
            for other in self._paragraphs_of(
                    self._table_by_name(doc, located.get("table") or "")
                    .getCellByName(located["cell"])):
                if _same_paragraph(other, paragraph):
                    break
                before += len(other.getString()) + 1
            span_start -= before
            span_end -= before
            shift = before

        # Comments are empty marker portions — Annotation ... AnnotationEnd
        # around a commented range, or a lone Annotation for a point anchor —
        # and they occupy no characters. Collect the portions first, then work
        # out which comment covers which stretch; attaching them while walking
        # counted a range comment twice.
        collected = []
        offset = 0
        portions = paragraph.createEnumeration()
        while portions.hasMoreElements():
            portion = portions.nextElement()
            kind = _get_property(portion, "TextPortionType", "Text")
            if kind in ("Annotation", "AnnotationEnd", "Redline"):
                # A recorded change marks its text exactly as a comment does:
                # empty portions of type "Redline" around it, carrying
                # RedlineType, RedlineAuthor and RedlineIdentifier, with
                # IsStart saying which end this is. Measured.
                collected.append((kind, offset, portion, ""))
                continue
            try:
                body = portion.getString()
            except Exception:
                continue
            collected.append(("Text", offset, portion, body))
            offset += len(body)

        spans = []
        pending = []
        for kind, at, portion, _body in collected:
            if kind == "Annotation":
                note = _get_property(portion, "TextField", None)
                if note is not None:
                    pending.append((note, at))
            elif kind == "AnnotationEnd" and pending:
                note, opened = pending.pop()
                spans.append((_describe_comment(note), opened, at))
        for note, at in pending:            # never closed: a point anchor
            spans.append((_describe_comment(note), at, at))

        changes = []
        open_changes = {}
        for kind, at, portion, _body in collected:
            if kind != "Redline":
                continue
            described = {
                "id": _get_property(portion, "RedlineIdentifier", "") or "",
                "kind": REDLINE_KINDS.get(
                    _get_property(portion, "RedlineType", "") or "",
                    (_get_property(portion, "RedlineType", "") or "").lower()),
                "author": _get_property(portion, "RedlineAuthor", "") or "",
            }
            if _get_property(portion, "IsStart", False):
                open_changes[described["id"]] = (described, at)
                continue
            opened = open_changes.pop(described["id"], None)
            changes.append((described, opened[1] if opened else at, at))
        for described, at in open_changes.values():
            changes.append((described, at, at))

        pictures = self._images_in(doc, index, span_start, span_end,
                                   paragraph_cursor)

        runs = []
        for kind, start_at, portion, body in collected:
            if kind != "Text" or not body:
                continue
            end_at = start_at + len(body)
            if end_at <= span_start or start_at >= span_end:
                continue

            clipped_start = max(start_at, span_start)
            clipped_end = min(end_at, span_end)
            described_run = self._describe_run(
                portion, body[clipped_start - start_at:clipped_end - start_at],
                index, clipped_start)
            described_run["address"] = self._address_in(
                located, clipped_start + shift, clipped_end - clipped_start)
            described_run["comments"] = [
                note for note, opened, closed in spans
                if (opened < end_at and closed > start_at)
                or (opened == closed and start_at <= opened < end_at)]
            # A recorded change covers text the way a comment does, and a
            # caller cannot see it any other way: its runs look ordinary.
            # A field carries the text it shows, so its run looks like any
            # other — and rewriting it destroys the field and leaves the
            # text. Measured: three fields in, none out.
            described_run["field"] = self._field_on(portion)
            # A footnote's mark is a character of the paragraph, and its run
            # looks like any other — rewriting it destroys the note and
            # leaves the number behind. Measured: one footnote in, none out.
            described_run["note"] = self._note_on(portion)
            described_run["changes"] = [
                change for change, opened, closed in changes
                if (opened < end_at and closed > start_at)
                or (opened == closed and start_at <= opened < end_at)]
            # A picture is an empty portion of type "Frame" at its anchor
            # offset, so it was skipped as an empty run and nothing reported
            # it. It travels on the run it is anchored inside.
            described_run["images"] = [
                image for image in pictures
                if clipped_start <= (image["address"] or {}).get("offset", -1)
                < clipped_end or (clipped_end == span_end
                                  and (image["address"] or {}).get("offset")
                                  == clipped_end)]
            runs.append(described_run)
        return runs

    def _field_on(self, portion: Any) -> Optional[Dict[str, Any]]:
        """What field a run *is*, when the run is one."""
        if _get_property(portion, "TextPortionType", "Text") != "TextField":
            return None
        field = _get_property(portion, "TextField", None)
        if field is None:
            return None
        return {"command": self._field_presentation(field, True),
                "text": self._field_presentation(field, False)}

    def _note_on(self, portion: Any) -> Optional[Dict[str, Any]]:
        """What footnote or endnote a run is the mark of, when it is one."""
        if _get_property(portion, "TextPortionType", "Text") != "Footnote":
            return None
        note = _get_property(portion, "Footnote", None)
        if note is None:
            return None
        endnote = False
        try:
            endnote = bool(note.supportsService("com.sun.star.text.Endnote"))
        except Exception as e:
            logger.info(f"A note would not say which kind it is: {e}")
        text = ""
        try:
            text = note.getText().getString()
        except Exception as e:
            logger.info(f"A note would not say what it holds: {e}")
        return {"kind": "endnote" if endnote else "footnote",
                "label": _get_property(note, "Label", "") or "",
                "text": _text_payload(text)["text"]}

    def _describe_run(self, portion: Any, body: str, paragraph: int,
                      offset: int) -> Dict[str, Any]:
        """One run as a caller sees it: its text, where it is, how it looks"""
        colour = _get_property(portion, "CharColor", -1)
        background = _get_property(portion, "CharBackColor", -1)
        weight = _get_property(portion, "CharWeight", 100.0) or 100.0
        posture = _get_property(portion, "CharPosture", None)
        return {
            "text": _text_payload(body)["text"],
            "length": len(body),
            "address": {"paragraph": paragraph, "offset": offset,
                        "length": len(body)},
            "bold": weight > 120.0,
            "italic": _is_italic(posture),
            "underline": bool(_get_property(portion, "CharUnderline", 0)),
            "font_name": _get_property(portion, "CharFontName"),
            "font_size": _get_property(portion, "CharHeight"),
            # -1 is "automatic", which is not a colour anyone chose
            "color": None if colour in (-1, None) else _colour_name(colour & 0xFFFFFF),
            "background_color": (None if background in (-1, None)
                                 else _colour_name(background & 0xFFFFFF)),
            "language": _locale_name(_get_property(portion, "CharLocale", None)),
            # A hyperlink is a property of the run, not of the text, and it is
            # lost outright by a plain replacement — which is what makes
            # reading it here the difference between keeping and destroying it.
            "link": _get_property(portion, "HyperLinkURL", "") or None,
            "link_target": _get_property(portion, "HyperLinkTarget", "") or None,
            "character_style": _get_property(portion, "CharStyleName", "") or None
        }

    def _flattening_loss(self, doc: Any, located: Dict[str, Any],
                         paragraph_cursor: Any) -> Optional[Dict[str, Any]]:
        """
        What a flat replacement of this range would destroy, or None

        setString over a range of several runs collapses them into one, and a
        hyperlink is lost even when it is the only run — both measured. A
        character style on a single run survives, so it is not a loss.
        """
        try:
            runs = self._runs_in(doc, located, paragraph_cursor)
        except Exception as e:
            # Unable to tell: better to let the edit through than to block it
            # on a failure to introspect.
            logger.info(f"Could not count the runs before replacing: {e}")
            return None

        links = [run for run in runs if run.get("link")]
        fields = [run for run in runs if run.get("field")]
        notes = [run for run in runs if run.get("note")]
        comments = _distinct_comments(runs)
        recorded = _distinct_changes(runs)
        pictures = _distinct_images(runs)
        inline = [image for image in pictures if image.get("inline")]
        if len(runs) <= 1 and not links and not comments and not inline \
                and not recorded and not fields and not notes:
            return None
        return {"runs": len(runs), "links": len(links),
                "comments": len(comments),
                "changes": len(recorded),
                "fields": len(fields),
                "notes": len(notes),
                "images": len(pictures), "inline_images": len(inline),
                "styles": len([r for r in runs if r.get("character_style")])}

    def _plan_run_rewrite(self, existing_runs: List[Dict[str, Any]],
                          prepared: List[tuple]) -> Dict[str, Any]:
        """
        Work out which runs to leave alone so their comments survive

        Rewriting text under a comment destroys the comment: the annotation
        must be created again, and a new one cannot carry back its id, its
        date, or the language its text was typed in. So a run whose text has
        not changed and which carries a comment is left alone.

        Two boundary facts, both measured on a live LibreOffice:

        * A stretch that begins exactly where a comment's anchor ends
          swallows the AnnotationEnd marker, and the comment goes with it.
          Beginning one character later keeps it, which is possible when that
          first character does not change; when it does, the comment cannot
          be kept and is written again instead.
        * A stretch that *ends* where a comment's anchor begins is harmless.
        """
        notes = []
        for note in _distinct_comments(existing_runs):
            covered = {position for position, run in enumerate(existing_runs)
                       if any(other is note for other in run.get("comments") or [])}
            if covered:
                notes.append((note, covered))

        # An inline picture is destroyed by a replacement of the text it sits
        # in, and unlike a comment it cannot be handed back through a tool
        # call: the only way to keep it is to leave that run alone.
        pictures = []
        for image in _distinct_images(existing_runs):
            if not image.get("inline"):
                continue
            covered = {position for position, run in enumerate(existing_runs)
                       if any(other.get("name") == image.get("name")
                              for other in run.get("images") or [])}
            if covered:
                pictures.append((image, covered))

        # A footnote's mark is one character of one run, and rewriting that
        # run destroys the note — measured, as with an inline picture. The
        # only way to keep it is to leave that run alone.
        marks = [(run["note"], {position})
                 for position, run in enumerate(existing_runs)
                 if run.get("note")]

        if not existing_runs or len(existing_runs) != len(prepared):
            return {"keep": set(), "kept": [],
                    "at_risk": [note for note, _ in notes],
                    "images_kept": [],
                    "images_at_risk": [image for image, _ in pictures],
                    "notes_kept": [],
                    "notes_at_risk": [mark for mark, _ in marks],
                    "segments": [{"first": 0, "last": len(prepared) - 1,
                                  "skip_first": False}]}

        unchanged = {position for position, (old, new)
                     in enumerate(zip(existing_runs, prepared))
                     if old["text"] == new[0]}
        attachments = notes + pictures + marks
        candidates = [(thing, covered) for thing, covered in attachments
                      if covered <= unchanged]

        while True:
            keep = set()
            for _thing, covered in candidates:
                keep |= covered
            # A comment only partly inside the kept runs would have its
            # markers rewritten anyway, so none of its runs may be kept.
            for _thing, covered in attachments:
                if covered - keep and covered & keep:
                    keep -= covered

            segments = []
            for position in range(len(prepared)):
                if position in keep:
                    continue
                if segments and segments[-1]["last"] == position - 1:
                    segments[-1]["last"] = position
                else:
                    segments.append({"first": position, "last": position,
                                     "skip_first": False})

            ends = {}
            for thing, covered in candidates:
                if covered <= keep:
                    last = max(covered)
                    ends[existing_runs[last]["address"]["offset"]
                         + existing_runs[last]["length"]] = thing

            giving_up = None
            for segment in segments:
                offset = existing_runs[segment["first"]]["address"]["offset"]
                if offset not in ends:
                    continue
                old_first = existing_runs[segment["first"]]["text"][:1]
                new_first = prepared[segment["first"]][0][:1]
                if old_first and old_first == new_first:
                    segment["skip_first"] = True
                else:
                    giving_up = ends[offset]
                    break

            if giving_up is None:
                return {"keep": keep,
                        "kept": [note for note, covered in notes
                                 if covered and covered <= keep],
                        "at_risk": [note for note, covered in notes
                                    if covered - keep],
                        "images_kept": [image for image, covered in pictures
                                        if covered and covered <= keep],
                        "images_at_risk": [image for image, covered in pictures
                                           if covered - keep],
                        "notes_kept": [mark for mark, covered in marks
                                       if covered and covered <= keep],
                        "notes_at_risk": [mark for mark, covered in marks
                                          if covered - keep],
                        "segments": segments}
            candidates = [(thing, covered) for thing, covered in candidates
                          if thing is not giving_up]

    def replace_runs(self, address: Any, runs: Any,
                     track_changes: Optional[bool] = None,
                     flatten: bool = False,
                     allow_protected: bool = False,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Replace a range with a sequence of runs, each formatted explicitly

        This is how text keeps its look through a translation. Formatting is
        applied per run afterwards rather than relied upon to be inherited:
        setString takes its properties from the surrounding text in ways that
        are not worth predicting.
        """
        doc, error = self._writer_document(doc, "Replacing runs")
        if error:
            return error

        if not isinstance(runs, (list, tuple)) or not runs:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": "runs must be a list with at least one run"}

        prepared = []
        for position, run in enumerate(runs):
            if not isinstance(run, dict) or not isinstance(run.get("text"), str):
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"run {position} needs a text string"}
            try:
                formatting = self._formatting_of(run)
                language = _locale(run["language"]) if run.get("language") else None
            except AddressError as e:
                return {"success": False, "code": "INVALID_PARAMETER", "error": f"run {position}: {e}"}
            comments = run.get("comments") or []
            if not isinstance(comments, (list, tuple)):
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"run {position}: comments must be a list"}
            prepared.append((run["text"], formatting, language, list(comments)))

        try:
            target = self._resolve_address(doc, address)
            located, located_cursor, _ = self._locate_range(
                doc, target, self._paragraph_hint(address, doc))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        protected = self._refuse_protected(doc, target, allow_protected)
        if protected:
            return protected

        # Measured: writing runs over a block of paragraphs collapsed three
        # paragraphs into one and answered success — the runs are one
        # paragraph's, and setString over the whole block takes the breaks
        # with it. Runs are written a paragraph at a time.
        block = self._block_of(address, located)
        if block and block[0] != block[1] and not flatten:
            return refusal(
                "WOULD_LOSE_FORMATTING",
                f"that address covers paragraphs {block[0]} to {block[1]}, and "
                f"writing runs over it would collapse them into one — the "
                f"paragraph breaks go with the rewrite. Write the runs of one "
                f"paragraph at a time (read_runs over a block reports each "
                f"run's own paragraph), or pass flatten=true to join them "
                f"knowingly",
                paragraphs=[block[0], block[1]])

        if located["paragraph"] is None and not located.get("cell"):
            return {"success": False, "code": "INVALID_ADDRESS",
                    "error": "That address is outside the body text and in no "
                             "table cell, so runs cannot be written into it"}

        paragraph = located["paragraph"]
        start = located["offset"]

        # Rewriting text under a comment destroys the comment: the annotation
        # has to be created again, and a new annotation cannot carry back
        # everything the old one had — its date, its id, and the language its
        # text was typed in, which UNO cannot write at all. So a run that
        # carries a comment and whose text has not changed is left alone, and
        # only what actually changes is rewritten. In a translation the
        # commented terms are usually the ones that stay.
        try:
            existing_runs = self._runs_in(doc, located, located_cursor)
        except Exception as e:
            logger.info(f"Could not read the runs before replacing: {e}")
            existing_runs = []

        plan = self._plan_run_rewrite(existing_runs, prepared)
        keep, segments = plan["keep"], plan["segments"]
        kept_notes, at_risk = plan["kept"], plan["at_risk"]

        def placements_for(positions, first_offset):
            """Where each carried comment goes, once per stretch it covers"""
            placed = []
            offset = first_offset
            for position in positions:
                text, _formatting, _language, comments = prepared[position]
                run_end = offset + len(text)
                for comment in comments:
                    key = _comment_key(comment)
                    extended = False
                    for placement in placed:
                        if placement["key"] == key and placement["end"] == offset:
                            placement["end"] = run_end
                            extended = True
                            break
                    if not extended:
                        placed.append({"key": key, "comment": comment,
                                       "start": offset, "end": run_end})
                offset = run_end
            return placed

        # A recorded change cannot travel on the runs the way a comment can:
        # it is Writer's own bookkeeping, and rewriting the text it marks
        # takes it away — which would quietly turn a reviewer's struck-out
        # deletion back into live text. Measured: two changes in, none out,
        # and the deleted word alive again in the paragraph.
        recorded = _distinct_changes(existing_runs)
        if recorded and not flatten:
            kinds = ", ".join(sorted({one["kind"] for one in recorded}))
            return refusal(
                "WOULD_LOSE_FORMATTING",
                f"This range carries {len(recorded)} recorded change"
                f"{'s' if len(recorded) > 1 else ''} ({kinds}), which a "
                f"rewrite would take away — a deletion waiting to be accepted "
                f"would come back as ordinary text. Settle them first with "
                f"accept_tracked_changes or reject_tracked_changes, or pass "
                f"flatten=true to write over them knowingly",
                changes=[{"id": one["id"], "kind": one["kind"],
                          "author": one["author"]} for one in recorded])

        if plan["images_at_risk"]:
            names = ", ".join(image.get("name") or "?"
                              for image in plan["images_at_risk"])
            return {
                "success": False,
                "code": "WOULD_LOSE_FORMATTING",
                "error": f"This range holds {len(plan['images_at_risk'])} "
                         f"inline picture"
                         f"{'s' if len(plan['images_at_risk']) > 1 else ''} "
                         f"({names}) in text you are changing, and replacing "
                         f"that text destroys the picture — a picture cannot "
                         f"be handed back the way a comment can. Pass the run "
                         f"holding it back with its text unchanged and rewrite "
                         f"the runs around it; read_runs says which run that is."
            }

        if plan["notes_at_risk"] and not flatten:
            kinds = ", ".join(sorted({one["kind"]
                                      for one in plan["notes_at_risk"]}))
            return refusal(
                "WOULD_LOSE_FORMATTING",
                f"This range holds the mark of {len(plan['notes_at_risk'])} "
                f"{kinds} in text you are changing, and rewriting that text "
                f"destroys the note — what it says would be gone, leaving "
                f"the number behind as ordinary text. Pass the run holding "
                f"the mark back unchanged and rewrite the runs around it; "
                f"read_runs says which run that is",
                notes=[{"kind": one["kind"], "text": one["text"]}
                       for one in plan["notes_at_risk"]])

        carried = sum(len(prepared[position][3]) for position in range(len(prepared))
                      if position not in keep)
        if at_risk and not carried:
            return {
                "success": False,
                "code": "WOULD_LOSE_FORMATTING",
                "error": f"This range carries {len(at_risk)} comment"
                         f"{'s' if len(at_risk) > 1 else ''} on text you are "
                         f"changing, and none of the runs you passed carries "
                         f"one, so they would be lost. Take the comments from "
                         f"read_runs and pass them back on the runs they "
                         f"belong to."
            }

        def edit():
            written_comments = 0
            renamed = {}                 # old comment id -> the new one
            replies_written = []         # (comment, note) for every reply
            rewritten = 0
            # Right to left, so the offsets of the earlier segments still hold
            # after a segment has been replaced with text of another length.
            for segment in reversed(segments):
                first, last = segment["first"], segment["last"]
                positions = list(range(first, last + 1))
                new_text = "".join(prepared[position][0]
                                   for position in positions)
                if keep:
                    span_start = existing_runs[first]["address"]["offset"]
                    span_length = sum(existing_runs[position]["length"]
                                      for position in positions)
                    # Rewriting a stretch that begins exactly where a kept
                    # comment's anchor ends swallows its AnnotationEnd marker
                    # and the comment with it, so such a stretch starts one
                    # character later — which the planner only allows when
                    # that character does not change.
                    written_from = span_start + (1 if segment["skip_first"] else 0)
                    span = self._resolve_address(
                        doc, self._address_in(
                            located, written_from,
                            span_length - (1 if segment["skip_first"] else 0)))
                    span.setString(new_text[1:] if segment["skip_first"]
                                   else new_text)
                else:
                    span_start = start
                    target.setString(new_text)
                rewritten += len(positions)

                offset = span_start
                for position in positions:
                    text, formatting, language, _comments = prepared[position]
                    if text and (formatting or language):
                        run_span = self._resolve_address(
                            doc, self._address_in(located, offset, len(text)))
                        if formatting:
                            self._apply_character_formatting(run_span, formatting)
                        if language is not None:
                            run_span.CharLocale = language
                    offset += len(text)

                for placement in placements_for(positions, span_start):
                    span = self._resolve_address(
                        doc, self._address_in(
                            located, placement["start"],
                            placement["end"] - placement["start"]))
                    made = self._anchor_comment(doc, span,
                                                placement["comment"])
                    written_comments += 1
                    was = placement["comment"].get("id")
                    if was:
                        renamed[was] = _get_property(made, "Name", "") or ""
                    if placement["comment"].get("reply_to"):
                        replies_written.append((placement["comment"],
                                                made))

            # A re-anchored comment is a new annotation with a new id, so a
            # reply written beside its parent named the parent's *old* id.
            # Now that every new id is known, the threads are joined again.
            for comment, note in replies_written:
                wanted = renamed.get(comment["reply_to"])
                if not wanted:
                    continue          # the parent was kept, so its id stands
                try:
                    note.ParentName = wanted
                except Exception as e:
                    logger.info(f"Could not keep a reply with its parent: {e}")

            return {"runs": len(prepared), "runs_rewritten": rewritten,
                    "runs_kept": len(keep), "paragraph": paragraph,
                    "characters": sum(len(text) for text, _, _, _ in prepared),
                    "comments_kept": len(kept_notes),
                    "comments_written": written_comments,
                    "images_kept": len(plan["images_kept"])}

        return self._guarded_edit(doc, "MCP: replace runs", track_changes, edit)
