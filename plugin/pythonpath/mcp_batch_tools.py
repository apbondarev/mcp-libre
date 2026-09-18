"""One call that carries out a plan, and one Ctrl+Z that takes it back."""

from typing import Any, Dict, List, Optional
import logging

from uno_values import AddressError

logger = logging.getLogger(__name__)

# A batch holds the server for as long as it runs — no step can be timed out
# on its own — so it is kept to a size a caller can wait for.
MAX_BATCH_STEPS = 50

WHEN_A_STEP_FAILS = ("stop", "continue", "undo")


class BatchTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_batch(self):
        """The tools of this part, as clients see them."""
        self.tools["batch_live"] = {
            "description": "Carry out several tool calls in order, as one edit: everything they write collapses into a single undo step, so the reader takes the whole plan back with one Ctrl+Z instead of twelve. Use it for a plan already worked out — translating a section paragraph by paragraph, colouring the tokens of a code block, commenting several places — where nothing later depends on what an earlier step returns, since a step cannot read another's result. Every step is checked before any of them runs (the tool must exist, its parameters must be an object), but a step can still fail on the document, and what happens then is on_error's business",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "description": "The calls to make, in order: [{\"tool\": \"replace_runs_live\", \"parameters\": {…}}, …]. At most 50, and none of them batch_live",
                        "items": {
                            "type": "object",
                            "properties": {
                                "tool": {"type": "string", "description": "Name of the tool to call"},
                                "parameters": {"type": "object", "description": "Its arguments, as that tool takes them"}
                            },
                            "required": ["tool"]
                        }
                    },
                    "on_error": {
                        "type": "string",
                        "enum": list(WHEN_A_STEP_FAILS),
                        "description": "What to do when a step fails: \"stop\" leaves the steps before it done and reports (the default), \"continue\" carries on with the rest, \"undo\" takes the whole batch back so the document is as it was",
                        "default": "stop"
                    },
                    "undo_title": {
                        "type": "string",
                        "description": "What the reader sees in the Undo menu; defaults to naming the batch and its size"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document. A step that names a different document is refused, since one batch is one document's undo step"
                    }
                },
                "required": ["steps"]
            },
            "handler": self.batch_live
        }

    def _check_steps(self, steps: Any, document: Optional[str]) -> Any:
        """The steps as they will be run, or a refusal naming the bad one."""
        if not isinstance(steps, list) or not steps:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": "steps must be a list holding at least one "
                             "{\"tool\": …, \"parameters\": …}"}
        if len(steps) > MAX_BATCH_STEPS:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": f"a batch takes at most {MAX_BATCH_STEPS} steps, "
                             f"{len(steps)} were given"}

        checked = []
        for position, step in enumerate(steps):
            where = f"step {position}"
            if not isinstance(step, dict):
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"{where} must be an object with a tool and "
                                 f"its parameters"}
            name = step.get("tool")
            if name == "batch_live":
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"{where} is a batch of its own; a batch does "
                                 f"not hold batches"}
            if name not in self.tools:
                return {"success": False, "code": "NOT_FOUND",
                        "error": f"{where} names no tool of this server: "
                                 f"{name!r}"}
            parameters = step.get("parameters", {})
            if parameters is None:
                parameters = {}
            if not isinstance(parameters, dict):
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"{where} must give its parameters as an "
                                 f"object, got {type(parameters).__name__}"}
            named = parameters.get("document")
            if named and document and named != document:
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"{where} acts on {named}, while the batch is "
                                 f"an undo step of {document} — make a batch "
                                 f"for each document"}
            checked.append((name, parameters))
        return checked

    # Paragraph numbers outside an address: the paragraph a block goes in
    # front of.
    NUMBERED_PARAMETERS = {"move_paragraph_live": ("to",),
                           "copy_paragraphs_live": ("to",)}

    def _numbers_in(self, name: str, parameters: Dict[str, Any]) -> set:
        """Every paragraph number a step's parameters name"""
        found = set()

        def walk(value):
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return
            if not any(key in value for key in ("anchor", "table", "cell",
                                                "selection")):
                for key in ("paragraph", "through", "heading"):
                    number = value.get(key)
                    if isinstance(number, int) and not isinstance(number, bool):
                        found.add(number)
            for inner in value.values():
                walk(inner)

        walk(parameters)
        for key in self.NUMBERED_PARAMETERS.get(name, ()):
            number = parameters.get(key)
            if isinstance(number, int) and not isinstance(number, bool):
                found.add(number)
        return found

    def _as_numbered_now(self, doc: Any, name: str,
                         parameters: Dict[str, Any], pins: Dict[int, str]):
        """(parameters with today's numbers, {old: new} where one moved).

        Raises AddressError when a pinned paragraph has gone — merged into
        the one before it or removed — so the step is refused rather than
        run against its neighbour.
        """
        import copy

        moved: Dict[int, int] = {}

        def now(number):
            if number not in pins:
                return number
            current = self.uno_bridge.paragraph_now(doc, pins[number])
            if current != number:
                moved[number] = current
            return current

        def walk(value):
            if isinstance(value, list):
                return [walk(item) for item in value]
            if not isinstance(value, dict):
                return value
            pinnable = not any(key in value for key in
                               ("anchor", "table", "cell", "selection"))
            out = {}
            for key, inner in value.items():
                if pinnable and key in ("paragraph", "through", "heading") \
                        and isinstance(inner, int) \
                        and not isinstance(inner, bool):
                    out[key] = now(inner)
                else:
                    out[key] = walk(inner)
            return out

        translated = walk(copy.deepcopy(parameters))
        for key in self.NUMBERED_PARAMETERS.get(name, ()):
            number = translated.get(key)
            if isinstance(number, int) and not isinstance(number, bool):
                translated[key] = now(number)
        return translated, moved

    def batch_live(self, steps: Any, on_error: str = "stop",
                   undo_title: Optional[str] = None,
                   document: Optional[str] = None) -> Dict[str, Any]:
        """Run several tools in order, as one undo step"""
        if on_error not in WHEN_A_STEP_FAILS:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": f"on_error is one of "
                             f"{', '.join(WHEN_A_STEP_FAILS)}, got "
                             f"{on_error!r}"}

        doc, error = self._target_document(document)
        if error:
            return error

        checked = self._check_steps(steps, document)
        if isinstance(checked, dict):
            return checked

        # Every paragraph the plan names by number is held before the first
        # step, and each step is given the number that paragraph has *then*.
        # Measured on a real document: a batch of ten edits by number, while
        # the reader pressed Enter above, wrote none of its ten paragraphs —
        # the typing lands between the steps of one call — and a plan whose
        # own steps insert paragraphs used to have to be written backwards.
        numbers = set()
        for name, parameters in checked:
            numbers |= self._numbers_in(name, parameters)
        pins = self.uno_bridge.pin_paragraph_numbers(doc, sorted(numbers))

        title = undo_title or f"MCP: batch of {len(checked)} steps"
        group = self.uno_bridge.open_undo_group(doc, title)
        results: List[Dict[str, Any]] = []
        failed = 0
        stopped_at = None
        try:
            for position, (name, parameters) in enumerate(checked):
                moved = {}
                try:
                    parameters, moved = self._as_numbered_now(
                        doc, name, parameters, pins)
                    if document and "document" not in parameters \
                            and "document" in self.tools[name].get(
                                "parameters", {}).get("properties", {}):
                        # The batch was told which document it is for, so its
                        # steps are: left alone, a step acts on the active
                        # one — which may be another document than the one
                        # whose paragraphs were pinned and whose undo group
                        # is open.
                        parameters = dict(parameters, document=document)
                    outcome = self._run_tool(name, parameters)
                except AddressError as e:
                    outcome = {"success": False, "code": "INVALID_ADDRESS",
                               "error": f"{e}; the step was not run"}
                worked = bool(outcome.get("success", True)) \
                    if isinstance(outcome, dict) else True
                entry = {"step": position, "tool": name,
                         "success": worked, "result": outcome}
                if moved:
                    # The paragraph the plan meant had moved, and was
                    # followed: said, so nobody has to wonder why step 7
                    # wrote paragraph 140 when it asked for 131.
                    entry["paragraphs_moved"] = {str(old): new
                                                 for old, new in moved.items()}
                results.append(entry)
                if worked:
                    continue
                failed += 1
                if on_error in ("stop", "undo"):
                    stopped_at = position
                    break
        finally:
            self.uno_bridge.close_undo_group(group)
            if pins:
                self.uno_bridge.drop_anchors(list(pins.values()), doc=doc)

        undone = False
        if failed and on_error == "undo":
            undone = self.uno_bridge.undo_group(group)

        done = sum(1 for one in results if one["success"])
        logger.info(f"Batch of {len(checked)}: {done} done, {failed} failed"
                    + (", taken back" if undone else ""))
        outcome = {"success": failed == 0,
                   "steps": len(checked),
                   "paragraphs_pinned": len(pins),
                   "done": done,
                   "failed": failed,
                   "not_run": len(checked) - len(results),
                   "undone": undone,
                   "undo_title": title,
                   "results": results}
        beyond = sorted(numbers - set(pins))
        if beyond:
            # Numbers past the end of the document when the batch began: left
            # as they were, since an earlier step may be meant to make them.
            outcome["paragraphs_not_pinned"] = beyond
        if failed:
            first = next(one for one in results if not one["success"])
            # The batch's own code is the failing step's, so a caller branches
            # on what actually went wrong rather than on "a step failed".
            outcome["code"] = (first["result"] or {}).get("code", "FAILED")
            outcome["error"] = (
                f"step {first['step']} ({first['tool']}) failed: "
                f"{(first['result'] or {}).get('error', 'no reason given')}")
            if stopped_at is not None and not undone:
                outcome["note"] = (
                    f"the {done} steps before it stand; one Ctrl+Z in the "
                    f"document takes the whole batch back, or run it again "
                    f"with on_error=\"undo\"")
        return outcome
