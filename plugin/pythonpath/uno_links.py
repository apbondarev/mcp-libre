"""Hyperlinks as a subject of their own: what a document points at.

Setting a link was already possible through `format_range`, and `read_runs`
reports the one a run carries. What was missing is the document-wide view,
and taking a link away without touching the words it is on.

Measured on a real document (34 links, 32 distinct URLs):

  * a link is `HyperLinkURL` on the run, with `HyperLinkTarget` ("_blank")
    and `HyperLinkName` beside it, and its navy underline comes from the
    character styles `UnvisitedCharStyleName` ("Internet link") and
    `VisitedCharStyleName` ("Visited Internet Link") — while `CharStyleName`
    itself is **empty**;
  * clearing `HyperLinkURL` alone leaves the look behind: `CharStyleName`
    then reads "Internet link" and the text stays blue and underlined.
    Clearing the two link styles as well is what makes it ordinary text
    again — measured, in that order;
  * the text itself is untouched either way, which is the point of the tool;
  * a link into the same document is a URL beginning with `#`, optionally
    naming what kind of thing it points at after a `|` ("#Name|outline").
    That target can be checked here; an http one cannot be, since nothing in
    this server may reach the network, so `broken` is left unknown for it.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import _get_property, _text_payload, refusal

logger = logging.getLogger(__name__)

LINK_PROPERTIES = ("HyperLinkURL", "HyperLinkTarget", "HyperLinkName",
                   "UnvisitedCharStyleName", "VisitedCharStyleName")

PICK = ("Say which links in exactly one way: address for a part of the "
        "document, url for every link pointing at one place, or all=true for "
        "every link in it")


class LinksMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _window_of(self, scope: Dict[str, Any]) -> Optional[tuple]:
        """The paragraphs a scope covers, so a walk can stop at them.

        Reading every portion of a 300-paragraph document takes a second and
        a half over the bridge, and a caller asking about one section should
        not pay it — the scope already knows which paragraphs it means.
        """
        if not isinstance(scope, dict):
            return None
        pair = scope.get("paragraphs")
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            return int(pair[0]), int(pair[1])
        one = scope.get("paragraph")
        if isinstance(one, int) and not isinstance(one, bool):
            return one, one
        return None

    def _links_in(self, doc: Any,
                  window: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Every hyperlink in the body, in one walk of it.

        Consecutive portions carrying the same URL are one link: a bold word
        inside a link is a portion of its own, and reporting it separately
        would count one link twice.
        """
        found: List[Dict[str, Any]] = []
        try:
            paragraphs = doc.getText().createEnumeration()
        except Exception as e:
            logger.error(f"Could not walk the document for its links: {e}")
            return found

        first, last = window if window else (0, None)
        index = 0
        while paragraphs.hasMoreElements():
            paragraph = paragraphs.nextElement()
            if not hasattr(paragraph, "createEnumeration"):
                continue
            if last is not None and index > last:
                break
            if index < first:
                index += 1
                continue
            offset = 0
            current = None
            portions = paragraph.createEnumeration()
            while portions.hasMoreElements():
                portion = portions.nextElement()
                body = portion.getString()
                url = _get_property(portion, "HyperLinkURL", "") or ""
                if url:
                    if current and current["url"] == url \
                            and current["_end"] == offset:
                        current["text"] += body
                        current["_end"] = offset + len(body)
                    else:
                        if current:
                            found.append(current)
                        current = {
                            "url": url,
                            "text": body,
                            "target": _get_property(portion, "HyperLinkTarget",
                                                    "") or "",
                            "name": _get_property(portion, "HyperLinkName",
                                                  "") or "",
                            "paragraph": index,
                            "offset": offset,
                            "_end": offset + len(body),
                        }
                elif current:
                    found.append(current)
                    current = None
                offset += len(body)
            if current:
                found.append(current)
                current = None
            index += 1

        targets = None
        for link in found:
            link["address"] = {"paragraph": link["paragraph"],
                               "offset": link["offset"],
                               "length": link["_end"] - link["offset"]}
            link.pop("_end", None)
            link.pop("paragraph", None)
            link.pop("offset", None)
            link["text"] = _text_payload(link["text"])["text"]
            link["internal"] = link["url"].startswith("#")
            if link["internal"]:
                if targets is None:
                    targets = self._link_targets(doc)
                wanted = link["url"][1:].split("|", 1)[0]
                link["points_at"] = wanted
                link["broken"] = wanted not in targets
            else:
                # Nothing here may reach the network, so an http link is
                # neither claimed sound nor claimed broken.
                link["broken"] = None
        return found

    def _link_targets(self, doc: Any) -> set:
        """Every name a link inside this document could point at"""
        names = set()
        for getter in ("getBookmarks", "getTextSections", "getReferenceMarks",
                       "getTextFrames", "getTextTables", "getGraphicObjects"):
            try:
                names.update(getattr(doc, getter)().getElementNames())
            except Exception as e:
                logger.info(f"Could not ask {getter} for its names: {e}")
        try:
            from uno_values import _heading_level
            for paragraph, _index in self._body_paragraphs(doc):
                if _heading_level(paragraph) > 0:
                    names.add(paragraph.getString())
        except Exception as e:
            logger.info(f"Could not gather the headings: {e}")
        return names

    def list_hyperlinks(self, address: Any = None,
                        doc: Any = None) -> Dict[str, Any]:
        """
        The hyperlinks of a document, with what each one points at

        A link into the same document says whether its target is still there;
        one pointing outside cannot be checked at all from here, and says so
        by leaving `broken` unknown rather than guessing.
        """
        doc, error = self._writer_document(doc, "Listing hyperlinks")
        if error:
            return error

        try:
            covers, scope = self._comment_scope(doc, address)
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        links = [link for link in self._links_in(doc, self._window_of(scope))
                 if covers(link["address"])]
        return {"success": True, "links": links, "count": len(links),
                "distinct_urls": len({link["url"] for link in links}),
                "internal": sum(1 for link in links if link["internal"]),
                "broken": sum(1 for link in links if link["broken"]),
                "scope": scope}

    def remove_hyperlink(self, address: Any = None, url: Optional[str] = None,
                         all: bool = False,
                         track_changes: Optional[bool] = None,
                         doc: Any = None) -> Dict[str, Any]:
        """
        Take hyperlinks away, leaving the words they were on

        Clearing the URL alone leaves the blue underline behind — it comes
        from two character styles, not from a colour — so those go too.
        """
        doc, error = self._writer_document(doc, "Removing a hyperlink")
        if error:
            return error
        picked = [one for one in (address is not None, url is not None,
                                  bool(all)) if one]
        if len(picked) != 1:
            return refusal("INVALID_PARAMETER", PICK)

        if address is not None:
            try:
                covers, _scope = self._comment_scope(doc, address)
            except Exception as e:
                return refusal("INVALID_ADDRESS", e)
        else:
            def covers(_address):
                return True

        window = None
        if address is not None:
            try:
                _covers, scope = self._comment_scope(doc, address)
                window = self._window_of(scope)
            except Exception as e:
                logger.info(f"Could not narrow the walk: {e}")
        wanted = [link for link in self._links_in(doc, window)
                  if covers(link["address"])
                  and (url is None or link["url"] == url)]
        if not wanted:
            return refusal("NOT_FOUND",
                           "there is no hyperlink there; list_hyperlinks says "
                           "where the links of this document are")

        def edit():
            removed = []
            # Right to left, so the addresses of the earlier links still hold
            # — nothing here changes the text, but the habit costs nothing.
            for link in sorted(wanted,
                               key=lambda one: (one["address"]["paragraph"],
                                                one["address"]["offset"]),
                               reverse=True):
                try:
                    span = self._resolve_address(doc, link["address"])
                except Exception as e:
                    logger.info(f"Could not reach a link to remove it: {e}")
                    continue
                for prop in LINK_PROPERTIES:
                    try:
                        setattr(span, prop, "")
                    except Exception as e:
                        logger.info(f"Could not clear {prop}: {e}")
                removed.append({"text": link["text"], "url": link["url"],
                                "address": link["address"]})
            removed.reverse()
            return {"removed": len(removed), "links": removed,
                    "text_kept": True}

        return self._guarded_edit(doc, "MCP: remove hyperlinks", track_changes,
                                  edit)
