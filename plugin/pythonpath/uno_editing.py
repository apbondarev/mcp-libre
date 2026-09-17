"""Changing text, under the guards every mutation shares.

_guarded_edit refuses a read-only document, makes one undo step of the whole
edit, honours the three states of track_changes and puts the document's own
setting back. A replacement that would flatten formatting, destroy a comment,
a picture or a table is refused before anything is written.
"""

from typing import Any, Optional, Dict
import logging
from uno_values import (AddressError, WRITER_SERVICE, _get_property, 
    _is_readonly, _locale, _locale_name, _supports, refusal)

logger = logging.getLogger(__name__)


class EditingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def insert_text(self, text: str, position: Optional[int] = None, doc: Any = None) -> Dict[str, Any]:
        """
        Insert text into a document
        
        Args:
            text: Text to insert
            position: Position to insert at (None for current cursor position)
            doc: Document to insert into (None for active document)
            
        Returns:
            Result dictionary
        """
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"success": False, "code": "NO_DOCUMENT", "error": "No active document"}
            
            # Handle Writer documents
            if _supports(doc, WRITER_SERVICE):
                text_obj = doc.getText()
                
                if position is None:
                    # Insert at current cursor position
                    cursor = doc.getCurrentController().getViewCursor()
                else:
                    # Insert at specific position
                    cursor = text_obj.createTextCursor()
                    cursor.gotoStart(False)
                    cursor.goRight(position, False)
                
                text_obj.insertString(cursor, text, False)
                logger.info(f"Inserted {len(text)} characters into Writer document")
                return {"success": True, "message": f"Inserted {len(text)} characters"}
            
            # Handle other document types
            else:
                return {"success": False, "code": "WRONG_DOCUMENT_TYPE", "error": f"Text insertion not supported for {self._get_document_type(doc)}"}
                
        except Exception as e:
            logger.error(f"Failed to insert text: {e}")
            return refusal("FAILED", e)

    def replace_selection(self, text: str, track_changes: Optional[bool] = None,
                          language: Optional[str] = None,
                          flatten: bool = False,
                          doc: Any = None) -> Dict[str, Any]:
        """
        Replace the selected text

        insert_text cannot do this: it calls insertString with bAbsorb=False,
        which inserts at the start of the selection and leaves the original
        behind — asking an assistant to translate and replace produced both
        texts.
        """
        return self._replace(
            {"selection": True}, text, track_changes, doc,
            what="Replacing the selection",
            undo_title="MCP: replace selection",
            empty_error="Nothing is selected, so there is nothing to replace. "
                        "Select the text first, or use a tool that inserts.",
            language=language, flatten=flatten)

    def replace_range(self, address: Any, text: str,
                      track_changes: Optional[bool] = None,
                      language: Optional[str] = None,
                      flatten: bool = False,
                      doc: Any = None) -> Dict[str, Any]:
        """
        Replace the text at an address

        This is what makes the addresses from get_outline and find_text
        actionable without a human selecting anything, which is what an
        assistant needs to rewrite a heading or every match of a search.
        An empty paragraph is a legitimate target, so emptiness is no error
        here, unlike with a selection.
        """
        # A replacement over a point is an insertion, and calling it a
        # replacement hides one: it wrote text into a document that was only
        # meant to be asked whether the edit would be refused. Filling an
        # empty *paragraph* is still a replacement, so only an address that
        # asks for a point is refused.
        asks_for_a_point = isinstance(address, dict) and (
            bool(address.get("selection")) or address.get("length") == 0)
        return self._replace(address, text, track_changes, doc,
                             what="Replacing text",
                             undo_title="MCP: replace text",
                             empty_error=("That address points at no text — a "
                                          "range of length 0 — so there is "
                                          "nothing to replace. Use insert_text "
                                          "to add text at a point, or give a "
                                          "length" if asks_for_a_point else None),
                             language=language,
                             flatten=flatten)

    def _replace(self, address: Any, text: str, track_changes: Optional[bool],
                 doc: Any, what: str, undo_title: str,
                 empty_error: Optional[str],
                 language: Optional[str] = None,
                 flatten: bool = False) -> Dict[str, Any]:
        """
        Rewrite the range an address points at, as a single undo step

        track_changes has three states, because two were not enough. None, the
        default, leaves the document's own recording setting alone: the edit is
        recorded if the document records, and the result says which happened.
        True records this edit even in a document that does not. False refuses
        to record it even in a document that does — the opt-out has to actually
        opt out, since a recorded replacement keeps the original struck through
        and reads as the replacement having failed. Either override is undone
        afterwards, so the document keeps the setting its owner chose.
        """
        doc, error = self._writer_document(doc, what)
        if error:
            return error

        if not isinstance(text, str):
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": f"text must be a string, got {type(text).__name__}"}

        if _is_readonly(doc):
            return {"success": False, "code": "READ_ONLY",
                    "error": "The document is read-only, so it cannot be edited"}

        try:
            locale = _locale(language) if language else None
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        replaced = target.getString()
        if empty_error and not replaced:
            return {"success": False, "code": "INVALID_ADDRESS", "error": empty_error}

        loss = None
        try:
            located, paragraph_cursor, _ = self._locate_range(
                doc, target, self._paragraph_hint(address, doc))
            paragraph_index = located["paragraph"]
            if paragraph_index is not None:
                loss = self._flattening_loss(doc, located, paragraph_cursor)
        except Exception as e:
            # Naming the paragraph is a nicety; failing to do so must not stop
            # the edit, and must not escape as an exception either.
            logger.info(f"Could not locate the range: {e}")
            paragraph_index = None

        # A range that runs through a table takes the table with it: setString
        # over such a range left no table at all — measured.
        tables = []
        try:
            tables = self._tables_in(doc, target)
        except Exception as e:
            logger.info(f"Could not tell whether the range holds a table: {e}")
        if tables and not flatten:
            named = ", ".join(f"{table['name']} ({table['rows']}x"
                              f"{table['columns']})" for table in tables)
            return {
                "success": False,
                "code": "WOULD_LOSE_FORMATTING",
                "error": f"This range runs through {len(tables)} table"
                         f"{'s' if len(tables) > 1 else ''} ({named}), and "
                         f"replacing it with a string destroys them — the "
                         f"whole table, not just its text. Read them with "
                         f"read_table, edit the text outside them, or pass "
                         f"flatten=true to lose them."
            }

        if loss and not flatten:
            details = [f"{loss['runs']} formatted runs"]
            if loss["links"]:
                details.append(f"{loss['links']} hyperlink"
                               f"{'s' if loss['links'] > 1 else ''}")
            if loss["comments"]:
                details.append(f"{loss['comments']} comment"
                               f"{'s' if loss['comments'] > 1 else ''}")
            if loss.get("changes"):
                details.append(f"{loss['changes']} recorded change"
                               f"{'s' if loss['changes'] > 1 else ''}")
            if loss.get("fields"):
                details.append(f"{loss['fields']} field"
                               f"{'s' if loss['fields'] > 1 else ''}")
            if loss.get("notes"):
                details.append(f"{loss['notes']} footnote or endnote mark"
                               f"{'s' if loss['notes'] > 1 else ''}")
            if loss.get("inline_images"):
                details.append(f"{loss['inline_images']} inline picture"
                               f"{'s' if loss['inline_images'] > 1 else ''}")
            if loss["styles"]:
                details.append(f"{loss['styles']} with character styles")
            destroyed = (" An inline picture is destroyed outright, not just "
                         "flattened." if loss.get("inline_images") else "")
            if loss.get("fields"):
                # A field writes its own text, and the run it makes looks
                # like any other — rewriting it leaves the text and takes the
                # field, which is how a date stops being a date.
                destroyed += (f" A field is destroyed outright by a rewrite, "
                              f"leaving behind whatever it happened to show.")
            if loss.get("notes"):
                # A footnote's mark is a character of the paragraph: rewrite
                # it and the note at the bottom of the page goes with it.
                destroyed += (f" A footnote or endnote is destroyed outright "
                              f"by a rewrite of its mark, and what it said "
                              f"goes with it — read it with list_notes first.")
            if loss.get("changes"):
                # A recorded change cannot be handed back the way a comment
                # can: it belongs to Writer's own recording. Settling it is
                # the way through, and there are tools for that now.
                destroyed += (f" The {loss['changes']} recorded change"
                              f"{'s' if loss['changes'] > 1 else ''} would go "
                              f"with it, struck-through text and all — accept "
                              f"or reject them first with "
                              f"accept_tracked_changes or "
                              f"reject_tracked_changes.")
            return {
                "success": False,
                "code": "WOULD_LOSE_FORMATTING",
                "error": f"This range holds {', '.join(details)}. Replacing it "
                         f"with one string would flatten them: inline code, "
                         f"italics and hyperlinks would be lost.{destroyed} "
                         f"Read it with read_runs, translate each run's text, "
                         f"and write it back with replace_runs — or pass "
                         f"flatten=true to accept the loss."
            }

        recording = bool(_get_property(doc, "RecordChanges", False))
        wanted = recording if track_changes is None else bool(track_changes)
        override = wanted != recording
        undo = _get_property(doc, "UndoManager", None)

        if undo:
            undo.enterUndoContext(undo_title)
        try:
            if override:
                doc.RecordChanges = wanted
            target.setString(text)
            if locale is not None:
                # Without this the new text keeps the locale of what it
                # replaced, and a translation is underlined word by word.
                target.CharLocale = locale
        except Exception as e:
            logger.error(f"Failed to replace text: {e}")
            return refusal("FAILED", e)
        finally:
            # Put the document's own setting back: this edit was recorded or
            # not as asked, but the owner's preference is not changed for them.
            if override:
                try:
                    doc.RecordChanges = recording
                except Exception as e:
                    logger.error(f"Could not restore RecordChanges: {e}")
            if undo:
                undo.leaveUndoContext()

        logger.info(f"Replaced {len(replaced)} characters with {len(text)}")
        return {
            "success": True,
            "replaced_length": len(replaced),
            "inserted_length": len(text),
            "paragraph": paragraph_index,
            "total_paragraphs": self._count_body_paragraphs(doc),
            "tracked": wanted,
            "language": _locale_name(locale) if locale is not None else None,
            "runs_flattened": loss["runs"] if loss else None,
            "links_dropped": loss["links"] if loss else None,
            "comments_dropped": loss["comments"] if loss else None,
            "changes_dropped": loss.get("changes") if loss else None,
            "fields_dropped": loss.get("fields") if loss else None,
            "notes_dropped": loss.get("notes") if loss else None,
            "images_dropped": loss.get("inline_images") if loss else None,
            "tables_dropped": len(tables) or None
        }

    def _guarded_edit(self, doc: Any, undo_title: str,
                      track_changes: Optional[bool], edit) -> Dict[str, Any]:
        """
        Run edit() as one undo step, under the guards every mutation shares

        Refuses a read-only document, honours the three states of
        track_changes and puts the document's own setting back, and reports
        what edit() returns alongside whether the change was recorded.
        """
        if _is_readonly(doc):
            return {"success": False, "code": "READ_ONLY",
                    "error": "The document is read-only, so it cannot be edited"}

        recording = bool(_get_property(doc, "RecordChanges", False))
        wanted = recording if track_changes is None else bool(track_changes)
        override = wanted != recording
        undo = _get_property(doc, "UndoManager", None)

        if undo:
            undo.enterUndoContext(undo_title)
        try:
            if override:
                doc.RecordChanges = wanted
            outcome = edit()
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        except Exception as e:
            logger.error(f"{undo_title} failed: {e}")
            return refusal("FAILED", e)
        finally:
            if override:
                try:
                    doc.RecordChanges = recording
                except Exception as e:
                    logger.error(f"Could not restore RecordChanges: {e}")
            if undo:
                undo.leaveUndoContext()

        result = {"success": True, "tracked": wanted}
        result.update(outcome or {})
        return result

    def open_undo_group(self, doc: Any, title: str) -> Optional[Dict[str, Any]]:
        """Start collapsing everything written from here into one undo entry.

        Measured: undo contexts nest, and the outer one is what the reader
        sees — three edits, each in a context of its own, inside one outer
        context left a single entry with the outer title, and one Ctrl+Z took
        all three back. Redo puts them all back too.
        """
        undo = _get_property(doc, "UndoManager", None)
        if undo is None:
            return None
        try:
            titles = list(undo.getAllUndoActionTitles())
            undo.enterUndoContext(title)
        except Exception as e:
            logger.info(f"Could not group the undo steps: {e}")
            return None
        return {"manager": undo, "entries_before": len(titles),
                "top_before": titles[0] if titles else None, "title": title}

    def close_undo_group(self, group: Optional[Dict[str, Any]]) -> None:
        """Close the group, so what follows is the reader's own work again."""
        if not group:
            return
        try:
            group["manager"].leaveUndoContext()
        except Exception as e:
            logger.error(f"Could not close an undo group: {e}")

    def undo_group(self, group: Optional[Dict[str, Any]]) -> bool:
        """Take back everything the group wrote, if it wrote anything.

        A context that wrote nothing leaves no entry at all — measured — so a
        bare undo() would take back whatever the reader did before the batch
        began. Counting the entries is not enough to tell one case from the
        other either: Writer's undo stack has a limit, and on a full one a
        new entry pushes the oldest out and the count does not move — which
        is how a failed batch first went untaken-back. So the top of the
        stack is watched as well as its size.
        """
        if not group:
            return False
        undo = group["manager"]
        try:
            titles = list(undo.getAllUndoActionTitles())
            top = titles[0] if titles else None
            wrote = (len(titles) > group["entries_before"]
                     or top != group["top_before"])
            if not wrote:
                return False
            undo.undo()
            return True
        except Exception as e:
            logger.error(f"Could not take back a batch: {e}")
            return False

    def set_language(self, address: Any, language: str,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Mark the text at an address as being in a language

        Writer decides which dictionary to spell-check a run against from its
        character locale, so a translation left with the original's locale is
        underlined word by word. This fixes text that is already written;
        replace_range and replace_selection take the same language up front.
        """
        doc, error = self._writer_document(doc, "Setting the language")
        if error:
            return error

        if _is_readonly(doc):
            return {"success": False, "code": "READ_ONLY",
                    "error": "The document is read-only, so it cannot be edited"}

        try:
            locale = _locale(language)
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        undo = _get_property(doc, "UndoManager", None)
        if undo:
            undo.enterUndoContext("MCP: set language")
        try:
            target.CharLocale = locale
        except Exception as e:
            logger.error(f"Failed to set the language: {e}")
            return refusal("FAILED", e)
        finally:
            if undo:
                undo.leaveUndoContext()

        logger.info(f"Marked text as {language}")
        return {
            "success": True,
            "language": _locale_name(locale),
            "characters": len(target.getString())
        }
