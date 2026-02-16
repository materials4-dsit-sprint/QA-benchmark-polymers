import re
from .document import DocumentParser, XMLDocumentParser

# Sections to skip (metadata, boilerplate - never include in body)
ACS_SKIP_SECTIONS = {
    'associated content', 'author information', 'acknowledgments',
    'acknowledgement', 'supporting information', 'conflict of interest',
    'notes', 'corresponding author', 'corresponding authors',
    'orcids', 'orcid', 'biographies'
}

DOI_PATTERN = re.compile(r'10\.\d{4,}/[^\s]+')
DOI_LINE_PATTERN = re.compile(r'DOI:\s*(10\.\d{4,}/[^\s]+)', re.I)
ACS_SECTION_PATTERN = re.compile(r'^##\s*[■\s]*(.+?)\s*$')
# Macdown/PDF conversion artifact: /uniXXXX -> Unicode char (e.g. /uniFB00 -> ﬀ, /uniFB01 -> ﬁ)
# Optional spaces around placeholder are artifacts (line breaks); remove them so "a /uniFB00 orded" -> "afforded"
UNICODE_PLACEHOLDER_PATTERN = re.compile(r' ?/uni([0-9A-Fa-f]{4}) ?')

class ACSParser(XMLDocumentParser):
    """
    XML document parser for ACS papers.
    
    """
    def __init__(self, filepath) -> None:
        super().__init__('acs', filepath)

        # ACS XML specific configs
        self.table_xpath = '//*[local-name()="table-wrap"]'
        self.title_xpath = '//*[local-name()="article-title"]'
        self.date_xpath = '//*[local-name()="pub-date" and @pub-type="ppub"]'
        self.journal_xpath = '//*[local-name()="journal-title"]'

    def parse_meta(self):
        super().parse_meta()

        # If date not found
        if self.date.strip() == "":
            self.date_xpath = '//*[local-name()="pub-date" and @date-type="pub"]'
            self.date = self.xpath_to_string(self.date_xpath)

    def parse_tables(self):
        # Table and figure reference numbers are not in inner texts.
        # Manually parse them from xref tags and add them add inner texts.

        pattern = re.compile(r"(\d+)")

        elems = self._tree.xpath('//*[local-name()="xref"]')
        for elem in elems:
            rid = elem.get("rid")

            # rid is an element ID
            dest_label = self._tree.find('//*[@id="{}"]/label'.format(rid))

            # extract the digit
            match = pattern.findall(rid)

            if dest_label is not None:
                par = dest_label.getparent()
                if par.tag.lower().startswith('table'):
                    elem.text = " Table " + dest_label.text
            elif len(match) == 1:
                if rid.startswith("tbl"):
                    elem.text = " Table " + match[0]
                elif rid.startswith("fig"):
                    elem.text = " Figure " + match[0]
    
        # Debugging purposes
        # self._tree.write("test.xml")

        # hand over to the parent parser
        return super().parse_tables()

    def parse_paragraphs(self):
        self.para_xpaths=['//*[local-name()="p"]']
        return super().parse_paragraphs()

class ACSMarkdownParser(DocumentParser):
    """Parser for ACS Markdown (PDF→Macdown). Strips noise, keeps title, DOI, sections, references."""

    def __init__(self, filepath: str) -> None:
        super().__init__('markdown', 'acs', filepath)
        self._content = ''
        self._lines = []
        self.doi = ''
        self.references = []
        self._sections_raw = {}
        self._load_markdown()

    def _load_markdown(self) -> None:
        with open(self.docpath, 'r', encoding='utf-8', errors='replace') as f:
            self._content = f.read()
        self._lines = self._content.splitlines()
    
    @staticmethod
    def _decode_unicode_placeholders(text: str) -> str:
        """Replace /uniXXXX Macdown placeholders with actual Unicode characters.
        E.g. /uniFB00 -> ﬀ (ff ligature), /uniFB01 -> ﬁ (fi ligature)."""
        def repl(m):
            try:
                return chr(int(m.group(1), 16))
            except (ValueError, OverflowError):
                return m.group(0)
        return UNICODE_PLACEHOLDER_PATTERN.sub(repl, text)

    def _extract_doi(self) -> str:
        for line in self._lines:
            m = DOI_LINE_PATTERN.search(line)
            if m:
                return m.group(1).rstrip('.,;')
        m = DOI_PATTERN.search(self._content)
        return m.group(0).rstrip('.,;') if m else ''

    def _normalize_section_name(self, name: str) -> str:
        """Strip ■ prefix and Roman numeral prefixes (I., II., III., etc.) for matching."""
        n = name.strip().lower()
        # Remove leading ■ and whitespace
        if n.startswith('■'):
            n = n[1:].strip()
        # Remove Roman numeral prefix: I., II., III., IV., V., VI., VII., VIII., IX., X.
        n = re.sub(r'^[ivxlcdm]+\.\s*', '', n, flags=re.I)
        return n.strip()

    def _is_main_section(self, name: str) -> bool:
        """Include section unless it's metadata/references. Supports both standard
        papers (Introduction, Results, Conclusion) and Perspective-style papers
        (I. PREAMBLE, II. PAST, III. PRECISION AND PERFECTION, etc.)."""
        n = self._normalize_section_name(name)
        if n == 'references':
            return False
        # Check if section matches any skip pattern
        for skip in ACS_SKIP_SECTIONS:
            if n == skip or n.startswith(skip + ' ') or (' ' + skip) in (' ' + n):
                return False
        # Include all other sections (main body, including abstract, preamble, etc.)
        return True
    
    def _clean_section_content(self, text: str) -> str:
        lines = text.splitlines()
        cleaned = []
        i = 0
        while i < len(lines):
            line = lines[i]
            s = line.strip()
            if s.startswith('<!--') and s.endswith('-->'):
                i += 1
                continue
            if s.lower() in ('received:', 'revised:', 'published:'):
                i += 2  # skip value line
                continue
            if re.match(r'^(January|...|December)\s+\d{1,2},?\s+\d{4}\s*$', s, re.I):
                i += 1
                continue
            if DOI_LINE_PATTERN.search(line):
                i += 1
                continue
            if re.match(r'^[A-Za-z\s]+\s+\d{4}\s*,\s*\d+\s*,\s*\d+\s*[-–]\s*\d+\s*$', s):
                i += 1
                continue
            if s == '*':
                i += 1
                continue
            cleaned.append(line)
            i += 1
        result = re.sub(r'\n{3,}', '\n\n', '\n'.join(cleaned)).strip()
        return self._decode_unicode_placeholders(result)

    def _split_sections(self) -> dict:
        sections = {}
        current_header, current_lines = None, []
        for line in self._lines:
            m = ACS_SECTION_PATTERN.match(line)
            if m:
                if current_header and current_lines:
                    sections[current_header] = '\n'.join(current_lines).strip()
                current_header = m.group(1).strip()
                current_lines = []
            elif current_header is not None:
                current_lines.append(line)
        if current_header and current_lines:
            sections[current_header] = '\n'.join(current_lines).strip()
        return sections
    
    def _extract_title(self) -> str:
        for line in self._lines:
            if line.startswith('## ') and '■' not in line[:15]:
                return self._decode_unicode_placeholders(line.lstrip('#').strip())
            m = ACS_SECTION_PATTERN.match(line)
            if m and m.group(1).strip().upper() != 'ABSTRACT':
                return self._decode_unicode_placeholders(m.group(1).strip())
        return self.docname.replace('.md', '').replace('-', ' ').title()

    def _parse_references(self, ref_text: str) -> list:
        refs = []
        for m in re.finditer(r'^\s*[-*]\s*\((\d+)\)\s+(.+?)(?=\s*[-*]\s*\(\d+\)\s+|\Z)', ref_text, re.DOTALL | re.M):
            body = re.sub(r'\s+', ' ', m.group(2).strip())
            refs.append(f"[{m.group(1)}] {self._decode_unicode_placeholders(body)}")
        return refs

    def _is_title_preamble_section(self, name: str) -> bool:
        """True if this section is the title/preamble (authors, affiliation, abstract) - exclude from body."""
        n = self._normalize_section_name(name)
        # Title section: matches paper title or has no Roman numeral / ■ (first section before main content)
        if not self.title:
            return False
        title_norm = self._normalize_section_name(self.title)
        return n == title_norm

    def _extract_abstract_from_preamble(self, text: str) -> str:
        """Extract abstract from preamble content (authors, affiliation, ABSTRACT: ...)."""
        text = self._clean_section_content(text)
        # Find ABSTRACT: and take the rest
        m = re.search(r'ABSTRACT:\s*(.+)', text, re.I | re.DOTALL)
        if m:
            return m.group(1).strip()
        return ''

    def parse_meta(self) -> None:
        self.doi = self._extract_doi()
        self.title = self._extract_title()
        all_sections = self._split_sections()
        for k, v in all_sections.items():
            if k.strip().upper() == 'ABSTRACT':
                self.abstract = self._clean_section_content(v)
                self.abstract = re.sub(r'^ABSTRACT:\s*', '', self.abstract, flags=re.I).strip()
                break
        # If no ABSTRACT section, try to extract from title/preamble section
        if not self.abstract:
            for k, v in all_sections.items():
                if self._is_title_preamble_section(k):
                    self.abstract = self._extract_abstract_from_preamble(v)
                    break
        body_parts = []
        for name, text in all_sections.items():
            if name.strip().upper() == 'REFERENCES':
                self.references = self._parse_references(text)
                continue
            if name.strip().lower() in ACS_SKIP_SECTIONS:
                continue
            # Skip title/preamble section (authors, affiliation, supporting info)
            if self._is_title_preamble_section(name):
                continue
            if self._is_main_section(name):
                body_parts.append(f"## {name}\n\n{self._clean_section_content(text)}")
        self.body = '\n\n'.join(body_parts)

    def parse_tables(self) -> None:
        self.tablesfound = 0

    def parse_paragraphs(self) -> None:
        pass

    def get_references_formatted(self) -> str:
        return '\n\n'.join(self.references)

    def get_chunks(self):
        """
        Split the document into sections and paragraphs.
        Returns list of Chunk(section=..., paragraphs=[...]).
        Call parse() first.
        """
        try:
            from ..chunker import chunk_markdown
        except ImportError:
            try:
                from chunker import chunk_markdown
            except ImportError:
                from chunker.markdown_chunker import chunk_markdown
        md = self.to_clean_markdown()
        return chunk_markdown(md)

    def to_clean_markdown(self) -> str:
        """Return clean Markdown: title, DOI, sections, references."""
        parts = [f"# {self.title}\n", f"**DOI:** {self.doi}\n" if self.doi else ""]
        if self.abstract:
            parts.append(f"## Abstract\n\n{self.abstract}\n")
        parts.append(self.body)
        if self.references:
            parts.append("\n## References\n\n" + self.get_references_formatted())
        return '\n'.join(p for p in parts if p)