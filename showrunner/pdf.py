"""PyMuPDF import shim (the module was renamed from `fitz` to `pymupdf`)."""
try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz


def open_pdf(path):
    return fitz.open(str(path))


def render(doc, page_number: int, width: int, height: int):
    """Render a page to fit inside width x height pixels. Returns (samples, w, h, stride)."""
    page = doc[page_number]
    zoom = min(width / page.rect.width, height / page.rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.samples, pix.width, pix.height, pix.stride
