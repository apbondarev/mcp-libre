"""Rendering a page as a picture, with LibreOffice alone.

The filter writer_png_Export renders the page the *view* is on — the page
properties in its FilterData are ignored — so a page is rendered by jumping
the view cursor there and putting it back. When that filter will not do it,
the page goes out as PDF and comes back through a hidden Draw document.
"""

import uno
from com.sun.star.beans import PropertyValue
from typing import Any, Optional, Dict
import base64
import tempfile
import os
import uuid
import logging
from uno_values import (AddressError, MAX_INLINE_IMAGE_BYTES, 
    MAX_RENDER_DPI, MAX_RENDER_PIXELS, MIN_RENDER_DPI, _file_url)

logger = logging.getLogger(__name__)


class RenderingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _page_of(self, doc: Any, address: Any) -> int:
        """
        Which page the text at an address is on

        Writer knows this only through the view cursor, so the cursor goes
        there and is put back where the reader left it.
        """
        controller = doc.getCurrentController()
        if not controller:
            raise AddressError("the document has no view, so it has no pages")
        view = controller.getViewCursor()
        target = self._resolve_address(doc, address)
        was = view.getStart()
        try:
            view.gotoRange(target, False)
            page = view.getPage()
        except Exception as e:
            raise AddressError(f"could not go to that address: {e}")
        finally:
            try:
                view.gotoRange(was, False)
            except Exception as e:
                logger.info(f"Could not put the view cursor back: {e}")
        return page

    def _page_pixels(self, doc: Any, page: int, dpi: int) -> tuple:
        """(width, height) in pixels for a page at `dpi`, from its own size"""
        width_mm100, height_mm100 = 21000, 29700          # A4, if all else fails
        try:
            for setting in doc.getRenderer(page - 1, doc, ()):
                if setting.Name == "PageSize":
                    width_mm100 = setting.Value.Width or width_mm100
                    height_mm100 = setting.Value.Height or height_mm100
        except Exception as e:
            logger.info(f"Could not read the size of page {page}: {e}")
        width = max(1, min(MAX_RENDER_PIXELS, round(width_mm100 * dpi / 2540)))
        height = max(1, min(MAX_RENDER_PIXELS, round(height_mm100 * dpi / 2540)))
        return width, height

    def _picture_filter_data(self, width: int, height: int) -> Any:
        """FilterData asking a picture filter for this many pixels"""
        pixel_width = PropertyValue()
        pixel_width.Name, pixel_width.Value = "PixelWidth", width
        pixel_height = PropertyValue()
        pixel_height.Name, pixel_height.Value = "PixelHeight", height
        return uno.Any("[]com.sun.star.beans.PropertyValue",
                       (pixel_width, pixel_height))

    def _store(self, document: Any, path: str, filter_name: str,
               filter_data: Any = None):
        """storeToURL with a filter, and its data when there is any"""
        name = PropertyValue()
        name.Name, name.Value = "FilterName", filter_name
        arguments = [name]
        if filter_data is not None:
            data = PropertyValue()
            data.Name, data.Value = "FilterData", filter_data
            arguments.append(data)
        document.storeToURL(_file_url(path), tuple(arguments))

    def _render_through_view(self, doc: Any, page: int, target: str,
                             width: int, height: int) -> bool:
        """Write the page as PNG with Writer's own filter, via the view"""
        controller = doc.getCurrentController()
        if not controller:
            return False
        view = controller.getViewCursor()
        was = None
        try:
            was = view.getStart()
        except Exception as e:
            logger.info(f"Could not remember where the cursor was: {e}")
        try:
            if view.getPage() != page:
                view.jumpToPage(page)
                if view.getPage() != page:
                    logger.info(f"The view would not go to page {page}")
                    return False
            self._store(doc, target, "writer_png_Export",
                        self._picture_filter_data(width, height))
        except Exception as e:
            logger.info(f"writer_png_Export could not render page {page}: {e}")
            return False
        finally:
            if was is not None:
                try:
                    view.gotoRange(was, False)
                except Exception as e:
                    logger.info(f"Could not put the view cursor back: {e}")
        return os.path.exists(target)

    def _render_through_draw(self, doc: Any, page: int, target: str,
                             width: int, height: int) -> Optional[str]:
        """The page as PDF, then through a hidden Draw document, as PNG"""
        pdf = os.path.join(tempfile.gettempdir(),
                           f"mcp_page_{uuid.uuid4().hex[:12]}.pdf")
        page_range = PropertyValue()
        page_range.Name, page_range.Value = "PageRange", str(page)
        try:
            self._store(doc, pdf, "writer_pdf_Export",
                        uno.Any("[]com.sun.star.beans.PropertyValue",
                                (page_range,)))
        except Exception as e:
            logger.error(f"Could not export page {page} as PDF: {e}")
            return None

        drawing = None
        try:
            hidden = PropertyValue()
            hidden.Name, hidden.Value = "Hidden", True
            importer = PropertyValue()
            importer.Name, importer.Value = "FilterName", "draw_pdf_import"
            # A bridge built without a local desktop (the standalone server,
            # the live checks) still has to be able to load a document.
            desktop = getattr(self, "desktop", None)
            if desktop is None:
                desktop = self.smgr.createInstanceWithContext(
                    "com.sun.star.frame.Desktop", self.ctx)
            drawing = desktop.loadComponentFromURL(
                _file_url(pdf), "_blank", 0, (importer, hidden))
            self._store(drawing, target, "draw_png_Export",
                        self._picture_filter_data(width, height))
        except Exception as e:
            logger.error(f"Could not render page {page} through Draw: {e}")
            return None
        finally:
            if drawing is not None:
                try:
                    drawing.close(False)
                except Exception as e:
                    logger.info(f"Could not close the Draw document: {e}")
            try:
                os.unlink(pdf)
            except OSError:
                pass
        return target if os.path.exists(target) else None

    def render_page(self, page: Optional[int] = None, address: Any = None,
                    dpi: int = 110, path: Optional[str] = None,
                    inline: bool = True, doc: Any = None) -> Dict[str, Any]:
        """
        A picture of one page, laid out as it would print

        This is the page as the document renders it — its layout, fonts,
        borders, tables and pictures, including changes that have not been
        saved. It is not a photograph of the screen: the spell checker's red
        underlines, the caret and the text boundary marks belong to Writer's
        window, not to the page. Nothing outside LibreOffice is used.
        """
        doc, error = self._writer_document(doc, "Rendering a page")
        if error:
            return error

        if not isinstance(dpi, int) or isinstance(dpi, bool) \
                or not MIN_RENDER_DPI <= dpi <= MAX_RENDER_DPI:
            return {"success": False,
                    "error": f"dpi must be a whole number between "
                             f"{MIN_RENDER_DPI} and {MAX_RENDER_DPI}, got "
                             f"{dpi!r}"}

        total = None
        try:
            total = doc.getRendererCount(doc, ())
        except Exception as e:
            logger.info(f"Could not count the pages: {e}")

        try:
            if address is not None:
                wanted = self._page_of(doc, address)
            elif page is not None:
                if not isinstance(page, int) or isinstance(page, bool) \
                        or page < 1:
                    raise AddressError(f"page must be a whole number from 1, "
                                       f"got {page!r}")
                wanted = page
            else:
                # Nothing asked for: the page the reader is looking at.
                wanted = doc.getCurrentController().getViewCursor().getPage()
        except AddressError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Could not work out which page to render: {e}")
            return {"success": False, "error": str(e)}

        if total and wanted > total:
            return {"success": False,
                    "error": f"This document has {total} pages, so there is "
                             f"no page {wanted}"}

        if path:
            target = os.path.abspath(os.path.expanduser(path))
            directory = os.path.dirname(target)
            if not os.path.isdir(directory):
                return {"success": False,
                        "error": f"There is no directory {directory} to write "
                                 f"into"}
        else:
            target = os.path.join(tempfile.gettempdir(),
                                  f"mcp_page_{wanted}_"
                                  f"{uuid.uuid4().hex[:8]}.png")
        if os.path.exists(target):
            try:
                os.unlink(target)
            except OSError as e:
                return {"success": False,
                        "error": f"Could not replace {target}: {e}"}

        width, height = self._page_pixels(doc, wanted, dpi)
        how = "writer_png_Export"
        if not self._render_through_view(doc, wanted, target, width, height):
            how = "PDF through a hidden Draw document"
            if self._render_through_draw(doc, wanted, target, width,
                                         height) is None:
                return {"success": False,
                        "error": f"LibreOffice would render neither page "
                                 f"{wanted} through writer_png_Export nor "
                                 f"through its PDF filter — see "
                                 f"/tmp/mcp_extension.log"}

        result = {"success": True, "page": wanted, "pages": total,
                  "path": target, "bytes": os.path.getsize(target),
                  "pixels": {"width": width, "height": height}, "dpi": dpi,
                  "rendered_by": how,
                  "shows": "the page as it prints; the spell checker's "
                           "underlines, the caret and the text boundaries are "
                           "Writer's window, not the page"}

        if inline:
            size = result["bytes"]
            if size > MAX_INLINE_IMAGE_BYTES:
                result["inline"] = False
                result["inline_refused"] = (
                    f"{size} bytes is more than the {MAX_INLINE_IMAGE_BYTES} a "
                    f"reply carries; read the file at {target}, or ask for a "
                    f"lower dpi")
            else:
                with open(target, "rb") as handle:
                    result["inline"] = True
                    result["_image_content"] = {
                        "data": base64.b64encode(handle.read()).decode("ascii"),
                        "mime_type": "image/png"}
        return result
