"""
Chunker for markdown documents: splits into sections, then paragraphs.
Works with clean markdown output (e.g. from ACSMarkdownParser.to_clean_markdown()).
After chunking: identifies caption paragraphs (Figure N., Table N.), extracts them to
a separate chunk, and merges the paragraphs above and below each caption.
"""
import re
from dataclasses import dataclass
from typing import Optional

# Paragraph is a caption if it starts with "Figure N." or "Table N."
CAPTION_PATTERN = re.compile(r'^(Figure\s+\d+|Table\s+\d+)[\.:]?\s*', re.I)


@dataclass
class Chunk:
    """A section with its paragraphs."""
    section: str
    paragraphs: list[str]


def _is_caption_paragraph(text: str) -> bool:
    """True if the paragraph is a Figure or Table caption."""
    return bool(CAPTION_PATTERN.match(text.strip()))


def _is_table_paragraph(text: str) -> bool:
    """True if the paragraph looks like a markdown table."""
    return '|' in text and text.count('|') >= 2


def _extract_captions_and_merge(chunks: list[Chunk]) -> list[Chunk]:
    """
    For each chunk: remove caption paragraphs, merge the paragraph above and below
    each caption, collect all captions into a separate chunk.
    """
    result: list[Chunk] = []
    all_captions: list[str] = []

    for chunk in chunks:
        new_paras: list[str] = []
        i = 0

        while i < len(chunk.paragraphs):
            para = chunk.paragraphs[i]
            if _is_caption_paragraph(para):
                all_captions.append(para)
                next_para = chunk.paragraphs[i + 1] if i + 1 < len(chunk.paragraphs) else None
                # Don't merge if next paragraph is a table - tables stay as separate blocks
                skip_merge = next_para is not None and _is_table_paragraph(next_para)
                # Merge paragraph above with paragraph below (unless below is a table)
                if new_paras and next_para is not None and not skip_merge:
                    new_paras[-1] = new_paras[-1].rstrip() + ' ' + next_para.lstrip()
                    i += 2
                    continue
                elif new_paras:
                    pass  # Caption at end
                elif next_para is not None and not skip_merge:
                    new_paras.append(next_para)
                    i += 2
                    continue
                i += 1
                continue

            new_paras.append(para)
            i += 1

        if new_paras:
            result.append(Chunk(section=chunk.section, paragraphs=new_paras))

    if all_captions:
        result.append(Chunk(section="Figures and Tables", paragraphs=all_captions))

    return result


def chunk_markdown(
    markdown: str,
    section_pattern: Optional[re.Pattern] = None,
    extract_captions: bool = True,
) -> list[Chunk]:
    """
    Split markdown into sections (by ## headers), then each section into paragraphs.
    Then: identify caption paragraphs (Figure N., Table N.), move them to a separate
    chunk, and merge the paragraphs above and below each caption.
    
    Args:
        markdown: Full markdown text (title, ## sections, etc.)
        section_pattern: Regex for section headers (default: ## Header)
        extract_captions: If True (default), extract Figure/Table captions and merge.
    
    Returns:
        List of Chunk(section=..., paragraphs=[...])
    """
    if section_pattern is None:
        section_pattern = re.compile(r'^##\s+(.+)$', re.MULTILINE)
    
    chunks: list[Chunk] = []
    sections = re.split(section_pattern, markdown)
    
    # First part may be preamble (title, DOI) before any ##
    # sections[0] = text before first ## (or full doc if no ##)
    # sections[1], sections[2], ... = header1, content1, header2, content2, ...
    if len(sections) == 1:
        # No ## headers - treat whole document as one section
        content = sections[0].strip()
        if content:
            paras = _split_paragraphs(content)
            chunks.append(Chunk(section="Document", paragraphs=paras))
        return chunks
    
    # sections[0] = preamble (before first ##)
    preamble = sections[0].strip()
    if preamble:
        paras = _split_paragraphs(preamble)
        if paras:
            chunks.append(Chunk(section="Preamble", paragraphs=paras))
    
    # Pairs of (header, content)
    for i in range(1, len(sections) - 1, 2):
        header = sections[i].strip()
        content = sections[i + 1].strip()
        paras = _split_paragraphs(content)
        chunks.append(Chunk(section=header, paragraphs=paras))
    
    if extract_captions:
        chunks = _extract_captions_and_merge(chunks)
    
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """
    Split text into paragraphs by double newlines.
    Preserves tables (lines containing |) as single paragraphs.
    """
    if not text.strip():
        return []
    
    # Split by double newline (or more)
    raw_blocks = re.split(r'\n\s*\n', text)
    
    paragraphs = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        # Normalize internal newlines to spaces for prose; keep table structure
        if '|' in block and block.count('|') >= 2:
            # Likely a table - keep as single block
            paragraphs.append(block)
        else:
            paragraphs.append(block)
    
    return paragraphs


class MarkdownChunker:
    """
    Chunker for markdown documents.
    Use chunk_markdown() for a simple function interface.
    """
    
    def __init__(self, section_pattern: Optional[re.Pattern] = None):
        self.section_pattern = section_pattern
    
    def chunk(self, markdown: str) -> list[Chunk]:
        return chunk_markdown(markdown, self.section_pattern)
    
    def chunk_to_dicts(self, markdown: str) -> list[dict]:
        """Return chunks as list of dicts for JSON serialization."""
        return [
            {"section": c.section, "paragraphs": c.paragraphs}
            for c in self.chunk(markdown)
        ]
