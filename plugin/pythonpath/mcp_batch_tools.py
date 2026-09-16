"""One call that carries out a plan, and one Ctrl+Z that takes it back."""

from typing import Any, Dict, List, Optional
import logging

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
            return {"success": False,
                    "error": "steps must be a list holding at least one "
                             "{\"tool\": …, \"parameters\": …}"}
        if len(steps) > MAX_BATCH_STEPS:
            return {"success": False,
                    "error": f"a batch takes at most {MAX_BATCH_STEPS} steps, "
                             f"{len(steps)} were given"}

        checked = []
        for position, step in enumerate(steps):
            where = f"step {position}"
            if not isinstance(step, dict):
                return {"success": False,
                        "error": f"{where} must be an object with a tool and "
                                 f"its parameters"}
            name = step.get("tool")
            if name == "batch_live":
                return {"success": False,
                        "error": f"{where} is a batch of its own; a batch does "
                                 f"not hold batches"}
            if name not in self.tools:
                return {"success": False,
                        "error": f"{where} names no tool of this server: "
                                 f"{name!r}"}
            parameters = step.get("parameters", {})
            if parameters is None:
                parameters = {}
            if not isinstance(parameters, dict):
                return {"success": False,
                        "error": f"{where} must give its parameters as an "
                                 f"object, got {type(parameters).__name__}"}
            named = parameters.get("document")
            if named and document and named != document:
                return {"success": False,
                        "error": f"{where} acts on {named}, while the batch is "
                                 f"an undo step of {document} — make a batch "
                                 f"for each document"}
            checked.append((name, parameters))
        return checked

    def batch_live(self, steps: Any, on_error: str = "stop",
                   undo_title: Optional[str] = None,
                   document: Optional[str] = None) -> Dict[str, Any]:
        """Run several tools in order, as one undo step"""
        if on_error not in WHEN_A_STEP_FAILS:
            return {"success": False,
                    "error": f"on_error is one of "
                             f"{', '.join(WHEN_A_STEP_FAILS)}, got "
                             f"{on_error!r}"}

        doc, error = self._target_document(document)
        if error:
            return error

        checked = self._check_steps(steps, document)
        if isinstance(checked, dict):
            return checked

        title = undo_title or f"MCP: batch of {len(checked)} steps"
        group = self.uno_bridge.open_undo_group(doc, title)
        results: List[Dict[str, Any]] = []
        failed = 0
        stopped_at = None
        try:
            for position, (name, parameters) in enumerate(checked):
                outcome = self._run_tool(name, parameters)
                worked = bool(outcome.get("success", True)) \
                    if isinstance(outcome, dict) else True
                results.append({"step": position, "tool": name,
                                "success": worked, "result": outcome})
                if worked:
                    continue
                failed += 1
                if on_error in ("stop", "undo"):
                    stopped_at = position
                    break
        finally:
            self.uno_bridge.close_undo_group(group)

        undone = False
        if failed and on_error == "undo":
            undone = self.uno_bridge.undo_group(group)

        done = sum(1 for one in results if one["success"])
        logger.info(f"Batch of {len(checked)}: {done} done, {failed} failed"
                    + (", taken back" if undone else ""))
        outcome = {"success": failed == 0,
                   "steps": len(checked),
                   "done": done,
                   "failed": failed,
                   "not_run": len(checked) - len(results),
                   "undone": undone,
                   "undo_title": title,
                   "results": results}
        if failed:
            first = next(one for one in results if not one["success"])
            outcome["error"] = (
                f"step {first['step']} ({first['tool']}) failed: "
                f"{(first['result'] or {}).get('error', 'no reason given')}")
            if stopped_at is not None and not undone:
                outcome["note"] = (
                    f"the {done} steps before it stand; one Ctrl+Z in the "
                    f"document takes the whole batch back, or run it again "
                    f"with on_error=\"undo\"")
        return outcome
