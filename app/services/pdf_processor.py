"""
PDF Processing Service

Extracts text and renders pages as images from PDF files for LLM analysis.

Uses PyMuPDF (fitz) for:
- Text extraction (structured, per-page)
- Page-to-image rendering (for diagrams, tables, charts the LLM can "see")

Strategy:
- Extract text from all pages (cheap, fast)
- Render first N pages as images (for visual content like P&IDs, flowsheets)
- Return both so the LLM gets text AND visual context
"""

import base64
import io
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Maximum pages to render as images (to limit payload size)
MAX_IMAGE_PAGES = 5
# DPI for page rendering (150 = good balance of quality vs size)
RENDER_DPI = 150
# Maximum text chars to extract (prevent huge PDFs from blowing up context)
MAX_TEXT_CHARS = 30_000


@dataclass
class PDFPage:
    """Extracted content from a single PDF page."""
    page_number: int
    text: str
    image_base64: Optional[str] = None  # PNG base64 of rendered page
    image_media_type: str = "image/png"


@dataclass
class PDFExtractionResult:
    """Complete extraction result from a PDF."""
    filename: str
    total_pages: int
    pages: List[PDFPage] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def full_text(self) -> str:
        """Combined text from all pages."""
        parts = []
        for p in self.pages:
            if p.text.strip():
                parts.append(f"--- Page {p.page_number} ---\n{p.text}")
        return "\n\n".join(parts)

    @property
    def image_pages(self) -> List[PDFPage]:
        """Pages that have rendered images."""
        return [p for p in self.pages if p.image_base64]


def extract_pdf(pdf_bytes: bytes, filename: str = "document.pdf") -> PDFExtractionResult:
    """
    Extract text and render pages from a PDF.

    Args:
        pdf_bytes: Raw PDF file bytes
        filename: Original filename for display

    Returns:
        PDFExtractionResult with text and page images
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF not installed. Run: pip install PyMuPDF")
        return PDFExtractionResult(
            filename=filename,
            total_pages=0,
            error="PDF processing unavailable (PyMuPDF not installed)"
        )

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.error(f"Failed to open PDF '{filename}': {e}")
        return PDFExtractionResult(
            filename=filename,
            total_pages=0,
            error=f"Failed to open PDF: {str(e)}"
        )

    total_pages = len(doc)
    pages_to_render = min(total_pages, MAX_IMAGE_PAGES)
    total_text_len = 0
    pages: List[PDFPage] = []

    for i in range(total_pages):
        page = doc[i]

        # --- Text extraction ---
        page_text = ""
        if total_text_len < MAX_TEXT_CHARS:
            try:
                page_text = page.get_text("text")
                total_text_len += len(page_text)
                # Truncate if we've exceeded the limit
                if total_text_len > MAX_TEXT_CHARS:
                    overshoot = total_text_len - MAX_TEXT_CHARS
                    page_text = page_text[: len(page_text) - overshoot]
                    page_text += "\n[... text truncated ...]"
            except Exception as e:
                logger.warning(f"Text extraction failed on page {i + 1}: {e}")

        # --- Image rendering (first N pages only) ---
        image_b64 = None
        if i < pages_to_render:
            try:
                zoom = RENDER_DPI / 72  # 72 is PDF default DPI
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)

                # Convert to PNG bytes
                png_bytes = pix.tobytes("png")
                image_b64 = base64.b64encode(png_bytes).decode("ascii")
            except Exception as e:
                logger.warning(f"Page render failed on page {i + 1}: {e}")

        pages.append(PDFPage(
            page_number=i + 1,
            text=page_text,
            image_base64=image_b64,
        ))

    doc.close()

    logger.info(
        f"PDF '{filename}': {total_pages} pages, "
        f"{pages_to_render} rendered as images, "
        f"{total_text_len} chars extracted"
    )

    return PDFExtractionResult(
        filename=filename,
        total_pages=total_pages,
        pages=pages,
    )


def pdf_to_llm_content_blocks(
    pdf_bytes: bytes,
    filename: str = "document.pdf",
) -> List[dict]:
    """
    Convert a PDF into a list of LLM content blocks (text + images).

    Returns content blocks in the orchestration format:
        [
            {"type": "text", "text": "PDF: document.pdf (5 pages)\\n---Page 1---\\n..."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}},
            ...
        ]

    These blocks are inserted into the user message's content list,
    alongside any text the user typed.
    """
    result = extract_pdf(pdf_bytes, filename)

    if result.error:
        return [{"type": "text", "text": f"[PDF Error: {result.error}]"}]

    blocks: List[dict] = []

    # Add page images first (LLM sees the visual content)
    for page in result.image_pages:
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": page.image_media_type,
                "data": page.image_base64,
            }
        })

    # Add extracted text as context
    text_header = f"📄 PDF: {filename} ({result.total_pages} page{'s' if result.total_pages != 1 else ''})"
    full_text = result.full_text
    if full_text.strip():
        blocks.append({
            "type": "text",
            "text": f"{text_header}\n\nExtracted text:\n{full_text}"
        })
    else:
        # Scanned PDF — no extractable text, images only
        blocks.append({
            "type": "text",
            "text": f"{text_header}\n\n(No extractable text — this appears to be a scanned/image-based PDF. Analyzing from rendered page images.)"
        })

    return blocks
