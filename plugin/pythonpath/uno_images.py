"""The pictures in a document: finding them, describing them, writing
them out.

A picture is an empty portion of type Frame at its anchor offset, costing no
characters, which is how reading runs came to skip it entirely. Selecting one
leaves no text selection at all — the selection *is* the picture.
"""

from com.sun.star.beans import PropertyValue
from typing import Any, Optional, Dict, List
import base64
import tempfile
import os
import re
import logging
from uno_values import (AddressError, GRAPHIC_SERVICE, IMAGE_EXTENSIONS, 
    IMAGE_TYPES, INLINE_ANCHOR, MAX_INLINE_IMAGE_BYTES, _anchor_kind, 
    _file_url, _get_property, _millimetres, _supports, _text_payload, refusal)

logger = logging.getLogger(__name__)


class ImagesMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _selected_graphics(self, doc: Any) -> List[Any]:
        """
        The pictures the reader has selected, if that is what is selected

        Selecting a picture in Writer makes the selection the picture itself
        — an SwXTextGraphicObject supporting com.sun.star.text.
        TextGraphicObject, with a name and no getCount — where selecting text
        makes it a collection of ranges. Asking it for a text range threw
        "the selection is not a text range: getCount", which is how a caller
        was left unable to say which of two pictures was in front of it.
        """
        controller = doc.getCurrentController()
        if not controller:
            return []
        try:
            selection = controller.getSelection()
        except Exception as e:
            logger.info(f"Could not read the selection: {e}")
            return []
        if selection is None:
            return []

        if _supports(selection, GRAPHIC_SERVICE):
            return [selection]

        # Several shapes selected together come as a collection.
        found = []
        try:
            for index in range(selection.getCount()):
                item = selection.getByIndex(index)
                if _supports(item, GRAPHIC_SERVICE):
                    found.append(item)
        except Exception:
            return []
        return found

    def _graphics(self, doc: Any) -> List[Any]:
        """Every picture in the document, in the order it names them"""
        try:
            graphics = doc.getGraphicObjects()
            return [graphics.getByName(name)
                    for name in graphics.getElementNames()]
        except Exception as e:
            logger.error(f"Could not enumerate the pictures: {e}")
            return []

    def _contents_of(self, portion: Any) -> List[Any]:
        """What a text portion holds — a picture, a frame — or nothing.

        Measured: a portion of type `Frame` hands out the object anchored
        there through `createContentEnumeration`, so a paragraph names its own
        pictures and nothing has to ask the document for all of them.
        """
        found = []
        try:
            contents = portion.createContentEnumeration(
                "com.sun.star.text.TextContent")
            while contents.hasMoreElements():
                found.append(contents.nextElement())
        except Exception as e:
            logger.info(f"A portion would not say what it holds: {e}")
        return found

    def _describe_image(self, doc: Any, image: Any, address: Any = "unplaced",
                        paragraph_text: Any = None) -> Dict[str, Any]:
        """
        A picture as a caller sees it: what it is, where it is, what it shows

        `address` is where its anchor sits in the body text, so it can be
        handed to any tool that reads or edits text, and `paragraph_text` is
        the text it is anchored to — the question a caller actually has.
        """
        anchor = _anchor_kind(_get_property(image, "AnchorType", None))
        described = {
            "name": _get_property(image, "Name", "") or "",
            "title": _get_property(image, "Title", "") or None,
            "description": _get_property(image, "Description", "") or None,
            "anchor": anchor,
            "inline": anchor == INLINE_ANCHOR,
            "width_mm": _millimetres(_get_property(image, "Width", None)),
            "height_mm": _millimetres(_get_property(image, "Height", None)),
        }

        graphic = _get_property(image, "Graphic", None)
        described["mime_type"] = None
        described["pixels"] = None
        described["linked"] = None
        described["origin_url"] = None
        if graphic is not None:
            mime = _get_property(graphic, "MimeType", "") or ""
            # image/x-vclgraphic means "whatever LibreOffice holds in memory",
            # which tells a caller nothing about the file it would get.
            described["mime_type"] = mime or None
            pixels = _get_property(graphic, "SizePixel", None)
            if pixels is not None:
                described["pixels"] = {
                    "width": _get_property(pixels, "Width", None),
                    "height": _get_property(pixels, "Height", None)}
            described["linked"] = bool(_get_property(graphic, "Linked", False))
            described["origin_url"] = _get_property(graphic, "OriginURL", "") \
                or None

        described["address"] = None
        described["paragraph_text"] = None
        if address != "unplaced":
            # The caller walked past this picture and knows where it stands;
            # locating it again walks the body.
            described["address"] = address
            described["paragraph_text"] = paragraph_text
            return described
        try:
            located, _, _ = self._locate_range(doc, image.getAnchor())
            described["address"] = located
            if located.get("paragraph") is not None:
                paragraph = self._paragraph_at(doc.getText(),
                                               located["paragraph"])
                if paragraph is not None:
                    described["paragraph_text"] = _text_payload(
                        paragraph.getString())["text"]
        except Exception as e:
            logger.info(f"Could not locate a picture: {e}")
        if described["address"] is None:
            page = _get_property(image, "AnchorPageNo", None)
            described["page"] = page if page else None
        return described

    def _images_in(self, doc: Any, paragraph: int, start: int,
                   end: int, paragraph_cursor: Any = None
                   ) -> List[Dict[str, Any]]:
        """The pictures anchored inside a stretch of a paragraph

        Describing a picture works out where its anchor sits, and that walks
        the body — so describing every picture in the document to find the
        ones in a single paragraph cost a walk per picture, on every call
        that reads runs. `paragraph_cursor` lets each anchor be compared with
        this paragraph first, three UNO calls apiece, and only the pictures
        that fall inside are described.
        """
        found = []
        for image in self._graphics(doc):
            if paragraph_cursor is not None \
                    and not self._anchored_in(doc, image, paragraph_cursor):
                continue
            described = self._describe_image(doc, image)
            address = described.get("address") or {}
            # A read made through an anchor knows no paragraph number — the
            # cursor comparison above is the filter then.
            if paragraph is not None and address.get("paragraph") != paragraph:
                continue
            offset = address.get("offset")
            if offset is None or not (start <= offset <= end):
                continue
            found.append(described)
        return found

    def _anchored_in(self, doc: Any, image: Any, paragraph_cursor: Any) -> bool:
        """Whether an object's anchor sits in this paragraph, asked cheaply

        A picture or a formula alike: what it needs is `getAnchor`.
        """
        try:
            anchor = image.getAnchor()
        except Exception as e:
            logger.info(f"An object would not say where it is: {e}")
            return True                   # let the slow path decide
        try:
            body = doc.getText()
            return self._covers(body, paragraph_cursor, anchor)
        except Exception as e:
            logger.info(f"Could not place a picture: {e}")
            return True

    def list_images(self, address: Any = None,
                    doc: Any = None) -> Dict[str, Any]:
        """
        The pictures of a document, a section, a paragraph, a range or the
        selection

        Each carries the address of its anchor, the text it is anchored to and
        its own size, so a caller can tell that a selection holds a picture,
        say where it is, and ask for the file with export_image.
        """
        doc, error = self._writer_document(doc, "Listing pictures")
        if error:
            return error

        selected = (self._selected_graphics(doc)
                    if isinstance(address, dict) and address.get("selection")
                    else [])
        if selected:
            images = [self._describe_image(doc, image) for image in selected]
            return {"success": True, "images": images, "count": len(images),
                    "scope": {"selection": "picture"}}

        try:
            covers, scope = self._comment_scope(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        images = []
        for image in self._graphics(doc):
            described = self._describe_image(doc, image)
            if not covers(described["address"]):
                continue
            images.append(described)

        images.sort(key=lambda i: (
            (i["address"] or {}).get("paragraph", 10 ** 9),
            (i["address"] or {}).get("offset", 0)))
        return {"success": True, "images": images, "count": len(images),
                "scope": scope}

    def export_image(self, name: Optional[str] = None,
                     path: Optional[str] = None,
                     image_format: str = "png", inline: bool = False,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Write a picture to a file, and hand back its bytes if asked

        `name` is the picture's own name, as list_images reports it; leave it
        out and the selected picture is taken, which is what "save the
        selected picture" means. The file is what LibreOffice re-encodes the
        picture into, so the size on disk is not the size it occupies in the
        document.
        """
        doc, error = self._writer_document(doc, "Exporting a picture")
        if error:
            return error

        known = []
        by_name = {}
        for image in self._graphics(doc):
            image_name = _get_property(image, "Name", "") or ""
            known.append(image_name)
            by_name[image_name] = image

        wanted = None
        selected = False
        if name is None or name == "":
            pictures = self._selected_graphics(doc)
            if len(pictures) == 1:
                wanted = pictures[0]
                name = _get_property(wanted, "Name", "") or ""
                selected = True
            elif len(pictures) > 1:
                names = ", ".join(_get_property(picture, "Name", "") or "?"
                                  for picture in pictures)
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"{len(pictures)} pictures are selected "
                                 f"({names}); name the one to write"}
            else:
                return {"success": False, "code": "NOT_FOUND",
                        "error": f"No picture is selected and none was named. "
                                 f"This document holds: "
                                 f"{', '.join(known) or 'none'}."}
        elif not isinstance(name, str):
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": "name must be the name of a picture, as "
                             "list_images reports it"}
        else:
            wanted = by_name.get(name)
        if wanted is None:
            return {"success": False, "code": "NOT_FOUND",
                    "error": f"No picture named {name} in this document. "
                             f"It holds: {', '.join(known) or 'none'}."}

        graphic = _get_property(wanted, "Graphic", None)
        if graphic is None:
            return {"success": False, "code": "FAILED",
                    "error": f"The picture {name} carries no graphic to write"}

        asked = (image_format or "png").lower()
        own = _get_property(graphic, "MimeType", "") or ""
        if asked == "original":
            mime = own if own in IMAGE_EXTENSIONS else "image/png"
        elif asked in IMAGE_TYPES:
            mime = IMAGE_TYPES[asked]
        else:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": f'format must be one of '
                             f'{", ".join(sorted(IMAGE_TYPES))} or "original", '
                             f'got {image_format!r}'}

        target = path
        if not target:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "image"
            target = os.path.join(tempfile.gettempdir(),
                                  f"{safe}.{IMAGE_EXTENSIONS[mime]}")
        target = os.path.abspath(os.path.expanduser(target))
        directory = os.path.dirname(target)
        if not os.path.isdir(directory):
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": f"There is no directory {directory} to write into"}

        try:
            provider = self.smgr.createInstanceWithContext(
                "com.sun.star.graphic.GraphicProvider", self.ctx)
            url = PropertyValue()
            url.Name, url.Value = "URL", _file_url(target)
            kind = PropertyValue()
            kind.Name, kind.Value = "MimeType", mime
            provider.storeGraphic(graphic, (url, kind))
        except Exception as e:
            logger.error(f"Could not write the picture {name}: {e}")
            return refusal("FAILED", e)

        if not os.path.exists(target):
            return {"success": False, "code": "FAILED",
                    "error": f"LibreOffice reported no error but wrote no "
                             f"file at {target}"}

        described = self._describe_image(doc, wanted)
        result = {"success": True, "name": name, "path": target,
                  "was_selected": selected,
                  "bytes": os.path.getsize(target), "mime_type": mime,
                  "pixels": described["pixels"], "address": described["address"],
                  "title": described["title"],
                  "description": described["description"]}

        if inline:
            size = os.path.getsize(target)
            if size > MAX_INLINE_IMAGE_BYTES:
                result["inline"] = False
                result["inline_refused"] = (
                    f"{size} bytes is more than the {MAX_INLINE_IMAGE_BYTES} "
                    f"a reply carries; read the file at {target} instead")
            else:
                with open(target, "rb") as handle:
                    encoded = base64.b64encode(handle.read()).decode("ascii")
                result["inline"] = True
                result["_image_content"] = {"data": encoded, "mime_type": mime}
        return result
