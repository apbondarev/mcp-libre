"""Footnotes and endnotes: a text of their own, hanging off one character.

Measured on a live Writer, and each fact shapes a tool or a guard:

  * **a note's anchor is a real character in the paragraph.** "A query" with
    a footnote after "query" reads `A query1 is the entry point`, the
    superscript number is one character of the string, and the portion is of
    type `Footnote` carrying the note in `Footnote`. So a note is the mirror
    of a comment in the same way a field is: it costs characters, and every
    offset after it counts them;
  * **a rewrite destroys it silently** — one footnote in, none out, with the
    number left behind as ordinary text — so `read_runs` reports `note` on
    the run that is one, the flatten guard counts them, and `replace_runs`
    refuses to rewrite the run an anchor sits on;
  * an **endnote supports `com.sun.star.text.Footnote` as well** as
    `com.sun.star.text.Endnote`, so the two are told apart by asking for
    Endnote, never by asking for Footnote;
  * `doc.getFootnotes()` and `doc.getEndnotes()` are *indexed*, not named,
    and a note carries no id of its own — `ReferenceId` was 0 for the first
    one — so a note is named by where its anchor sits, the way a field is;
  * `Label` is empty while Writer numbers the note itself, and setting it to
    "*" makes that the mark;
  * the note's own text is an `XText`: `note.getText().setString(...)`;
  * a footnote inside a **table cell** works, and its anchor addresses as a
    cell address.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import AddressError, _get_property, _text_payload, refusal

logger = logging.getLogger(__name__)

ENDNOTE_SERVICE = "com.sun.star.text.Endnote"
FOOTNOTE_SERVICE = "com.sun.star.text.Footnote"
NOTE_KINDS = ("footnote", "endnote")


class NotesMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _notes_of(self, doc: Any, getter: str) -> List[Any]:
        found = []
        try:
            held = getattr(doc, getter)()
        except Exception as e:
            logger.info(f"This document keeps no notes ({getter}): {e}")
            return found
        try:
            for index in range(held.getCount()):
                found.append(held.getByIndex(index))
        except Exception as e:
            logger.info(f"Could not read the notes: {e}")
        return found

    def _notes(self, doc: Any) -> List[Dict[str, Any]]:
        """Every footnote and endnote, described and placed in one walk"""
        held = [(note, "footnote")
                for note in self._notes_of(doc, "getFootnotes")]
        held += [(note, "endnote")
                 for note in self._notes_of(doc, "getEndnotes")]

        anchors, kept = [], []
        for note, kind in held:
            try:
                anchors.append(note.getAnchor())
            except Exception as e:
                logger.info(f"A note would not say where it is: {e}")
                continue
            kept.append((note, kind))
        placed = self._addresses_in_order(doc, anchors)

        found = []
        for (note, kind), anchor, address in zip(kept, anchors, placed):
            found.append({
                "kind": kind,
                "mark": self._safely(lambda: anchor.getString(), ""),
                "label": _get_property(note, "Label", "") or "",
                "text": _text_payload(
                    self._safely(lambda: note.getText().getString(), ""))["text"],
                "address": address,
                "note": note,
            })
        found.sort(key=lambda one: (
            (one["address"] or {}).get("paragraph")
            if (one["address"] or {}).get("paragraph") is not None else 10 ** 9,
            (one["address"] or {}).get("table") or "",
            (one["address"] or {}).get("cell") or "",
            (one["address"] or {}).get("offset") or 0))
        return found

    def _safely(self, read, fallback):
        try:
            return read()
        except Exception as e:
            logger.info(f"Could not read a note: {e}")
            return fallback

    def list_notes(self, address: Any = None, kind: Optional[str] = None,
                   doc: Any = None) -> Dict[str, Any]:
        """
        The footnotes and endnotes of a document, with their text and where
        their marks sit

        Scoped like the comments — the whole document, a section, a
        paragraph, a range or the selection.
        """
        doc, error = self._writer_document(doc, "Listing notes")
        if error:
            return error
        if kind is not None and kind not in NOTE_KINDS:
            return refusal("INVALID_PARAMETER",
                           f"kind is \"footnote\" or \"endnote\", got {kind!r}")

        try:
            covers, scope = self._comment_scope(doc, address)
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        notes = [{key: value for key, value in one.items() if key != "note"}
                 for one in self._notes(doc)
                 if covers(one["address"])
                 and (kind is None or one["kind"] == kind)]
        return {"success": True, "notes": notes, "count": len(notes),
                "footnotes": sum(1 for one in notes
                                 if one["kind"] == "footnote"),
                "endnotes": sum(1 for one in notes
                                if one["kind"] == "endnote"),
                "scope": scope}

    def add_note(self, address: Any, text: str, kind: str = "footnote",
                 label: Optional[str] = None,
                 track_changes: Optional[bool] = None,
                 doc: Any = None) -> Dict[str, Any]:
        """
        Put a footnote or an endnote after the text an address names

        The mark goes at the *end* of the address, where a writer would put
        it, and the note's text is a text of its own — the paragraph is left
        exactly as it was apart from that one character.
        """
        doc, error = self._writer_document(doc, "Adding a note")
        if error:
            return error
        if kind not in NOTE_KINDS:
            return refusal("INVALID_PARAMETER",
                           f"kind is \"footnote\" or \"endnote\", got {kind!r}")
        if not isinstance(text, str) or not text.strip():
            return refusal("INVALID_PARAMETER", "a note needs some text")

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        def edit():
            service = (ENDNOTE_SERVICE if kind == "endnote"
                       else FOOTNOTE_SERVICE)
            note = doc.createInstance(service)
            if label:
                note.Label = label
            owner = target.getText()
            owner.insertTextContent(target.getEnd(), note, False)
            note.getText().setString(text)
            anchor = note.getAnchor()
            located, _, _ = self._locate_range(doc, anchor)
            return {"kind": kind, "mark": self._safely(anchor.getString, ""),
                    "label": _get_property(note, "Label", "") or "",
                    "text": _text_payload(text)["text"],
                    "address": located}

        return self._guarded_edit(doc, f"MCP: add a {kind}", track_changes,
                                  edit)

    def _note_at(self, doc: Any, address: Any) -> tuple:
        """The one note an address names, or a refusal saying why not.

        A note has no id of its own, so it is named by where its mark sits —
        the same way a field is, and with the same refusal when an address
        covers more than one.
        """
        try:
            covers, _scope = self._comment_scope(doc, address)
        except Exception as e:
            return None, refusal("INVALID_ADDRESS", e)
        found = [one for one in self._notes(doc) if covers(one["address"])]
        if not found:
            return None, refusal(
                "NOT_FOUND",
                "there is no note there; list_notes says where the notes of "
                "this document are")
        if len(found) > 1:
            exact = [one for one in found if one["address"] == address]
            if len(exact) != 1:
                return None, refusal(
                    "INVALID_PARAMETER",
                    f"that covers {len(found)} notes; name one of them by its "
                    f"own address, as list_notes reports it",
                    notes=[one["address"] for one in found])
            found = exact
        return found[0], None

    def update_note(self, address: Any, text: Optional[str] = None,
                    label: Optional[str] = None,
                    track_changes: Optional[bool] = None,
                    doc: Any = None) -> Dict[str, Any]:
        """
        Change what a note says, or the mark it wears

        The document's own text is left alone: this edits the note at the
        bottom of the page, not the sentence that carries it. An empty label
        gives the note back to Writer's numbering.
        """
        doc, error = self._writer_document(doc, "Updating a note")
        if error:
            return error
        if text is None and label is None:
            return refusal("INVALID_PARAMETER",
                           "say what to change: text, label, or both")

        wanted, refused = self._note_at(doc, address)
        if refused:
            return refused

        def edit():
            note = wanted["note"]
            if text is not None:
                note.getText().setString(text)
            if label is not None:
                note.Label = label
            return {"kind": wanted["kind"],
                    "mark": self._safely(note.getAnchor().getString, ""),
                    "label": _get_property(note, "Label", "") or "",
                    "text": _text_payload(
                        self._safely(note.getText().getString, ""))["text"],
                    "address": wanted["address"]}

        return self._guarded_edit(doc, "MCP: update a note", track_changes,
                                  edit)

    def delete_note(self, address: Any,
                    track_changes: Optional[bool] = None,
                    doc: Any = None) -> Dict[str, Any]:
        """
        Take a note away, and its mark out of the sentence

        What the note said is in the result, since nothing else keeps it.
        """
        doc, error = self._writer_document(doc, "Deleting a note")
        if error:
            return error

        wanted, refused = self._note_at(doc, address)
        if refused:
            return refused

        def edit():
            note = wanted["note"]
            anchor = note.getAnchor()
            anchor.getText().removeTextContent(note)
            return {"deleted": wanted["kind"], "was_saying": wanted["text"],
                    "mark": wanted["mark"], "address": wanted["address"]}

        return self._guarded_edit(doc, "MCP: delete a note", track_changes,
                                  edit)
