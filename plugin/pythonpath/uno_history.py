"""Undo and redo: taking a step back, which nothing here could do.

Every edit this server makes is one undo step and a batch is one for the
whole plan — but an assistant that has just made a mess had to edit its way
out of it, while the reader could take it back with one Ctrl+Z. Measured on
a live Writer:

  * the manager is `doc.UndoManager`, and the methods are
    `getAllUndoActionTitles()` / `getAllRedoActionTitles()` (not
    "…Strings"), `getCurrentUndoActionTitle()`, `isUndoPossible()`,
    `isRedoPossible()`, `undo()`, `redo()`, `clear()` and `clearRedo()`.
    `undo` and `redo` do not show up in `dir()` on a pyuno proxy and work
    perfectly well;
  * **the stack is the document's, not ours.** The titles say whose a step
    is: our edits read "MCP: replace text", the reader's typing reads
    `Typing: “Третий”` and `New Paragraph`, and an edit made through the API
    without an undo context of its own reads `Insert $1`. So undoing blindly
    can take back what the human typed, and `undo` stops at the first step
    that is not this server's unless it is told otherwise;
  * undoing fills the redo list and redoing empties it again, both measured.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import _get_property, refusal

logger = logging.getLogger(__name__)

# Every undo step this server makes is titled this way — see _guarded_edit.
OURS = "MCP:"
MAX_STEPS = 100


class HistoryMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _undo_manager(self, doc: Any) -> Any:
        return _get_property(doc, "UndoManager", None)

    def _titles(self, manager: Any, getter: str) -> List[str]:
        try:
            return [str(title) for title in getattr(manager, getter)()]
        except Exception as e:
            logger.info(f"Could not read the {getter}: {e}")
            return []

    def _step(self, title: str, position: int) -> Dict[str, Any]:
        return {"step": position, "title": title,
                "made_here": title.startswith(OURS)}

    def list_undo_steps(self, limit: int = 20,
                        doc: Any = None) -> Dict[str, Any]:
        """
        What can be taken back, and what can be put back again

        The stack belongs to the document, so it holds the reader's typing
        beside this server's edits; each step says which it is, since undoing
        someone else's work is not the same act at all.
        """
        doc, error = self._writer_document(doc, "Reading the undo history")
        if error:
            return error
        manager = self._undo_manager(doc)
        if manager is None:
            return refusal("UNSUPPORTED", "this document keeps no undo history")

        undo = self._titles(manager, "getAllUndoActionTitles")
        redo = self._titles(manager, "getAllRedoActionTitles")
        return {
            "success": True,
            "undo": [self._step(title, position)
                     for position, title in enumerate(undo[:limit], start=1)],
            "redo": [self._step(title, position)
                     for position, title in enumerate(redo[:limit], start=1)],
            "can_undo": bool(undo), "can_redo": bool(redo),
            "undo_steps": len(undo), "redo_steps": len(redo),
            "ours_on_top": bool(undo) and undo[0].startswith(OURS),
        }

    def undo(self, steps: int = 1, include_others: bool = False,
             doc: Any = None) -> Dict[str, Any]:
        """
        Take the last edit back, or several — this server's own by default

        The undo stack is the document's: the reader's typing sits in it
        beside these tools' edits. So this stops at the first step that was
        not made here, and says which it stopped at; `include_others` takes
        back whatever is there.
        """
        return self._step_back(doc, steps, include_others, forward=False)

    def redo(self, steps: int = 1, include_others: bool = False,
             doc: Any = None) -> Dict[str, Any]:
        """Put back what was taken away, under the same rule as undo"""
        return self._step_back(doc, steps, include_others, forward=True)

    def _step_back(self, doc: Any, steps: Any, include_others: bool,
                   forward: bool) -> Dict[str, Any]:
        word = "Redoing" if forward else "Undoing"
        doc, error = self._writer_document(doc, word)
        if error:
            return error
        if isinstance(steps, bool) or not isinstance(steps, int) \
                or not 1 <= steps <= MAX_STEPS:
            return refusal("INVALID_PARAMETER",
                           f"steps is a whole number from 1 to {MAX_STEPS}, "
                           f"got {steps!r}")
        manager = self._undo_manager(doc)
        if manager is None:
            return refusal("UNSUPPORTED", "this document keeps no undo history")
        if self._is_readonly_document(doc):
            return refusal("READ_ONLY",
                           "the document is read-only, so nothing can be "
                           "taken back")

        getter = ("getAllRedoActionTitles" if forward
                  else "getAllUndoActionTitles")
        titles = self._titles(manager, getter)
        if not titles:
            return refusal("NOT_FOUND",
                           f"there is nothing to {'redo' if forward else 'undo'}"
                           f" in this document")

        wanted = titles[:steps]
        stopped = None
        if not include_others:
            taken = []
            for title in wanted:
                if not title.startswith(OURS):
                    stopped = title
                    break
                taken.append(title)
            wanted = taken
        if not wanted:
            return refusal(
                "INVALID_PARAMETER",
                f"the next step is {stopped!r}, which this server did not "
                f"make — the undo history belongs to the document, and the "
                f"reader's own work is in it. Pass include_others=true to "
                f"take it back anyway",
                next_step=stopped)

        done = []
        for title in wanted:
            try:
                manager.redo() if forward else manager.undo()
            except Exception as e:
                logger.info(f"{word} failed at {title!r}: {e}")
                break
            done.append(title)

        left = self._titles(manager, getter)
        other = self._titles(manager, "getAllUndoActionTitles" if forward
                             else "getAllRedoActionTitles")
        return {"success": True,
                "undone" if not forward else "redone": done,
                "steps": len(done),
                "stopped_at": stopped,
                "asked_for": steps,
                "can_undo": bool(self._titles(manager,
                                              "getAllUndoActionTitles")),
                "can_redo": bool(self._titles(manager,
                                              "getAllRedoActionTitles")),
                "next": (left[0] if left else None),
                "other_way": (other[0] if other else None)}

    def _is_readonly_document(self, doc: Any) -> bool:
        try:
            return bool(doc.isReadonly())
        except Exception as e:
            logger.info(f"Could not ask whether the document is read-only: {e}")
            return False
