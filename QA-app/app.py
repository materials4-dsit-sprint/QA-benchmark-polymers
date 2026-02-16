"""
PDF QA App: Upload PDF → Parse paragraphs → Add Q&A pairs → Export to JSON.

Run with: streamlit run app.py

Uses docling for PDF→markdown, ACSMarkdownParser for ACS-formatted markdown,
and chunk_markdown for chunking.
"""

import json
import re
import tempfile
import uuid
from pathlib import Path

import streamlit as st

from chunker import chunk_markdown

# Parser options for PDF/markdown origin
PARSER_OPTIONS = {"ACS": "acs", "Generic": "generic"}

# Macdown/PDF artifact: /uniXXXX -> Unicode (e.g. /uniFB00 -> ﬀ, /uniFB01 -> ﬁ)
_UNICODE_PLACEHOLDER = re.compile(r" ?/uni([0-9A-Fa-f]{4}) ?")


def _decode_unicode_placeholders(text: str) -> str:
    """Replace /uniXXXX placeholders with actual Unicode characters in parsed text."""
    def repl(m):
        try:
            return chr(int(m.group(1), 16))
        except (ValueError, OverflowError):
            return m.group(0)
    return _UNICODE_PLACEHOLDER.sub(repl, text)


# Sections to exclude (include all others)
EXCLUDED_SECTION_KEYWORDS = (
    "REFERENCE", "BIBLIOGRAPHY",
    "FIGURE", "TABLE",  # Figure/table captions, "Figures and Tables" chunk
    "PREAMBLE",
)


def _normalize_section(name: str) -> str:
    """Normalize section name for comparison. Strips ■, Roman numerals, etc."""
    n = name.strip()
    # Remove leading ■ and whitespace (ACS format)
    n = n.lstrip()
    while n.startswith("■"):
        n = n[1:].lstrip()
    # Remove Roman numeral prefix: I., II., III., 1., 2., etc.
    n = re.sub(r"^[IVXLCDM]+\.\s*", "", n, flags=re.I)
    n = re.sub(r"^\d+\.\s*", "", n)
    # Collapse spaces, uppercase
    return " ".join(n.upper().split())


def _sanitize_section(name: str) -> str:
    """Safe section string for Streamlit widget keys (no special symbols)."""
    safe = re.sub(r"[^\w\s-]", "", name)
    return re.sub(r"\s+", "_", safe.strip())[:50] or "Document"


def _build_answers(context: str, answer_text: str, is_impossible: bool) -> list[dict]:
    """Build SQuAD-style answers: [{"text": a, "answer_start": start}]."""
    if is_impossible or not answer_text:
        return []
    start = context.find(answer_text)
    if start < 0:
        start = -1  # Answer not exact span in context (paraphrase)
    return [{"text": answer_text, "answer_start": start}]


def _pdf_to_markdown_via_docling(uploaded_file) -> str:
    """Convert PDF to markdown using docling."""
    from docling.document_converter import DocumentConverter

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name
    try:
        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _get_chunks_via_acs_parser(filepath: str):
    """Use ACSMarkdownParser for ACS-formatted markdown (file path required)."""
    # Stub pylogg if not installed (optional dep from parent project)
    import sys
    if "pylogg" not in sys.modules:
        try:
            import pylogg
        except ImportError:
            _stub = type("Log", (), {"debug": lambda *a: None, "info": lambda *a: None})()
            sys.modules["pylogg"] = type("pylogg", (), {"New": lambda _: _stub})()
    from parsers.acs import ACSMarkdownParser

    parser = ACSMarkdownParser(filepath)
    parser.parse_meta()
    return parser.get_chunks()


def _get_chunks_from_markdown(markdown: str):
    """Use project chunker to split markdown into sections and paragraphs."""
    return chunk_markdown(markdown)


def extract_chunks(uploaded_file, parser: str = "acs") -> list[tuple[str, str]]:
    """
    Parse uploaded file (PDF or Markdown) using docling and parser.
    Returns list of (section, paragraph) tuples.
    Includes all sections except: References, Figures/Tables, Preamble.

    parser: "acs" (ACSMarkdownParser) or "generic" (chunk_markdown only)
    """
    name = uploaded_file.name.lower()
    use_acs = parser == "acs"

    if name.endswith(".md") or name.endswith(".markdown"):
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".md", delete=False
        ) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name
        try:
            if use_acs:
                try:
                    chunks = _get_chunks_via_acs_parser(tmp_path)
                except Exception:
                    with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    chunks = _get_chunks_from_markdown(content)
            else:
                with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                chunks = _get_chunks_from_markdown(content)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    else:
        # PDF: docling → markdown → chunker
        markdown = _pdf_to_markdown_via_docling(uploaded_file)
        chunks = _get_chunks_from_markdown(markdown)

    def _section_excluded(norm: str) -> bool:
        """True if section should be excluded (references, figures, preamble)."""
        if not norm:
            return True
        return any(kw in norm for kw in EXCLUDED_SECTION_KEYWORDS)

    # Flatten to (section, paragraph), excluding references, figures/tables, preamble
    result = []
    for chunk in chunks:
        if chunk.section == "Figures and Tables":
            continue  # Skip figure/table captions
        norm_section = _normalize_section(chunk.section)
        if _section_excluded(norm_section):
            continue
        for para in chunk.paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(para) < 100:
                continue  # Skip very short paragraphs
            if para.count("|") >= 4:
                continue  # Skip tables
            section_clean = _decode_unicode_placeholders(chunk.section)
            para_clean = _decode_unicode_placeholders(para)
            result.append((section_clean, para_clean))

    # Fallback: if exclusion was too aggressive, include all (except Figures and Tables)
    if len(result) == 0:
        for chunk in chunks:
            if chunk.section == "Figures and Tables":
                continue
            for para in chunk.paragraphs:
                para = para.strip()
                if not para or para.count("|") >= 4:
                    continue
                if len(para) >= 50:  # Slightly looser min in fallback
                    section_clean = _decode_unicode_placeholders(chunk.section)
                    para_clean = _decode_unicode_placeholders(para)
                    result.append((section_clean, para_clean))

    return result


def main():
    st.set_page_config(page_title="PDF QA Builder", page_icon="📄", layout="wide")
    st.title("📄 PDF QA Builder")
    st.markdown(
        "Upload a PDF → Parse paragraphs → Add Q&A pairs → Export to JSON"
    )

    # triples: list of {id, context, question, answer, section?}
    # id_map: (context, question) -> uuid for preserving on edit
    # form_version: incremented on Clear to reset widget keys
    if "triples" not in st.session_state:
        st.session_state.triples = []
    if "id_map" not in st.session_state:
        st.session_state.id_map = {}
    if "form_version" not in st.session_state:
        st.session_state.form_version = 0
    # para_qa_slots[i] = number of QA input pairs to show for paragraph i
    if "para_qa_slots" not in st.session_state:
        st.session_state.para_qa_slots = {}
    # skipped_paras: set of para indices user marked as "nothing useful"
    if "skipped_paras" not in st.session_state:
        st.session_state.skipped_paras = set()

    parser = st.sidebar.selectbox(
        "PDF / Markdown origin (parser)",
        options=list(PARSER_OPTIONS.keys()),
        index=0,
        help="ACS: for American Chemical Society papers. Generic: for other sources.",
    )
    parser_value = PARSER_OPTIONS[parser]

    uploaded = st.file_uploader("Upload PDF or Markdown", type=["pdf", "md", "markdown"])
    if not uploaded:
        st.info("Upload a PDF or Markdown to begin.")
        if st.session_state.triples:
            _render_export_section()
        return

    # Cache parsed chunks by file identity and parser
    cache_key = f"{uploaded.name}_{uploaded.size}_{parser_value}"
    if getattr(st.session_state, "parse_cache_key", None) != cache_key:
        st.session_state.chunks_cache = extract_chunks(uploaded, parser=parser_value)
        st.session_state.parse_cache_key = cache_key
        st.session_state.triples = []
        st.session_state.id_map = {}
        st.session_state.para_qa_slots = {}
        st.session_state.skipped_paras = set()
        st.session_state.form_version += 1
    chunks = st.session_state.chunks_cache
    n_total = len(chunks)
    n_skipped = len(st.session_state.skipped_paras)
    contexts_with_qa = {t["context"] for t in st.session_state.triples}
    st.sidebar.success(f"Parsed {n_total} chunks")
    n_questions = len(st.session_state.triples)
    n_paras_left = n_total - n_skipped - len(contexts_with_qa)
    st.sidebar.metric("Questions", n_questions)
    st.sidebar.metric("Paragraphs without QA", n_paras_left)
    if n_skipped > 0:
        if st.sidebar.button("Clear skipped paragraphs"):
            st.session_state.skipped_paras = set()
            st.rerun()

    filter_incomplete = st.sidebar.checkbox(
        "Show only paragraphs without QA",
        value=False,
        help="Focus on paragraphs that still need QA pairs.",
    )

    # Apply pending updates from previous run (before widgets are created)
    if "_pending_suggest" in st.session_state:
        a_key, suggested = st.session_state["_pending_suggest"]
        st.session_state[a_key] = suggested
        del st.session_state["_pending_suggest"]
    if "_pending_clear" in st.session_state:
        for k, val in st.session_state["_pending_clear"]:
            st.session_state[k] = val
        del st.session_state["_pending_clear"]

    # Build triples from form state this run
    # Preserve triples from filtered-out paragraphs when "Show only without QA" is on
    if filter_incomplete:
        hidden_contexts = {chunks[j][1] for j in range(len(chunks)) if j not in st.session_state.skipped_paras and chunks[j][1] in contexts_with_qa}
        preserved = [t for t in st.session_state.triples if t["context"] in hidden_contexts]
    else:
        preserved = []
    new_triples = list(preserved)

    for i, (section, para) in enumerate(chunks):
        if i in st.session_state.skipped_paras:
            continue
        if filter_incomplete and para in contexts_with_qa:
            continue  # Skip paragraphs that already have QA (preserved in new_triples)
        safe_sec = _sanitize_section(section)
        n_slots = st.session_state.para_qa_slots.get(i, 1)

        label = f"Paragraph {i + 1} ({safe_sec})"
        with st.expander(label, expanded=(i == 0)):
            st.markdown(f"**Context:**\n{para}")

            v = st.session_state.form_version
            for j in range(n_slots):
                q_key = f"q_p{i}_s{j}_{safe_sec}_v{v}"
                a_key = f"a_p{i}_s{j}_{safe_sec}_v{v}"
                imp_key = f"imp_p{i}_s{j}_{safe_sec}_v{v}"
                question = st.text_input(
                    "Question",
                    key=q_key,
                    placeholder="Enter your question...",
                )
                is_impossible = st.checkbox(
                    "No answer in context",
                    key=imp_key,
                    help="Check if the question cannot be answered from the context.",
                )
                answer = st.text_input(
                    "Answer (optional if no answer in context)",
                    key=a_key,
                    placeholder="Enter answer, or plausible answer for impossible Qs...",
                )
                col1, col2, _ = st.columns([1, 1, 4])
                with col1:
                    if question and st.button("Suggest answer", key=f"sug_p{i}_s{j}_{safe_sec}_v{v}"):
                        try:
                            if st.secrets.get("OPENAI_API_KEY"):
                                from openai import OpenAI
                                client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])
                                r = client.chat.completions.create(
                                    model="gpt-4o-mini",
                                    messages=[{
                                        "role": "user",
                                        "content": f"Extract the answer to this question from the context. Reply with ONLY the exact span from the context, or 'N/A' if not answerable.\n\nContext: {para}\n\nQuestion: {question}",
                                    }],
                                    temperature=0,
                                )
                                suggested = (r.choices[0].message.content or "").strip()
                                if suggested and suggested.upper() != "N/A":
                                    st.session_state["_pending_suggest"] = (a_key, suggested)
                                    st.rerun()
                            else:
                                st.warning("Set OPENAI_API_KEY in secrets for suggest")
                        except Exception as e:
                            st.error(str(e))

                if question:
                    lookup_key = (para, question)
                    qa_id = st.session_state.id_map.get(lookup_key)
                    if qa_id is None:
                        qa_id = str(uuid.uuid4())
                        st.session_state.id_map[lookup_key] = qa_id
                    answers = _build_answers(para, answer, is_impossible)
                    triple = {
                        "id": qa_id,
                        "context": para,
                        "question": question,
                        "is_impossible": is_impossible,
                        "answers": answers,
                    }
                    if section and "DOCUMENT" not in _normalize_section(section):
                        triple["section"] = safe_sec
                    new_triples.append(triple)

            cols = st.columns([1, 1, 4])
            with cols[0]:
                if st.button(f"Add another Q&A", key=f"add_p{i}_{safe_sec}_v{v}"):
                    st.session_state.para_qa_slots[i] = n_slots + 1
                    st.rerun()
            with cols[1]:
                if st.button("Skip paragraph", key=f"skip_p{i}_{safe_sec}_v{v}"):
                    st.session_state.skipped_paras.add(i)
                    st.rerun()

    st.session_state.triples = new_triples
    st.divider()
    _render_export_section()


def _render_export_section():
    """Render export UI: list triples (with id), download button, clear option."""
    st.subheader("Export")
    triples_list = st.session_state.triples
    if triples_list:
        json_str = json.dumps(triples_list, indent=2)
        st.download_button(
            "Download as JSON",
            data=json_str,
            file_name="qa_triples.json",
            mime="application/json",
        )
        with st.expander("Preview (SQuAD format: is_impossible, answers)"):
            st.json(triples_list)

        st.markdown("**Delete QA pair**")
        del_col1, del_col2, _ = st.columns([1, 1, 4])
        with del_col1:
            del_idx = st.number_input(
                "QA pair # (0-based)",
                min_value=0,
                max_value=max(0, len(triples_list) - 1),
                value=0,
                key="del_qa_idx",
            )
        with del_col2:
            if st.button("Delete") and triples_list:
                idx = int(del_idx)
                if 0 <= idx < len(triples_list):
                    removed = triples_list[idx]
                    ctx, q = removed.get("context"), removed.get("question")
                    # Remove from triples and id_map
                    st.session_state.triples = [t for i, t in enumerate(triples_list) if i != idx]
                    lookup = (ctx, q)
                    if lookup in st.session_state.id_map:
                        del st.session_state.id_map[lookup]
                    # Find widget keys to clear; defer to next run (before widgets are created)
                    v = st.session_state.form_version
                    chunks_cache = st.session_state.get("chunks_cache", [])
                    keys_to_clear = []
                    for i, (section, para) in enumerate(chunks_cache):
                        if para != ctx:
                            continue
                        safe_sec = _sanitize_section(section)
                        n_slots = st.session_state.para_qa_slots.get(i, 1)
                        for j in range(n_slots):
                            q_key = f"q_p{i}_s{j}_{safe_sec}_v{v}"
                            if st.session_state.get(q_key) == q:
                                a_key = f"a_p{i}_s{j}_{safe_sec}_v{v}"
                                imp_key = f"imp_p{i}_s{j}_{safe_sec}_v{v}"
                                keys_to_clear = [(q_key, ""), (a_key, ""), (imp_key, False)]
                                break
                        else:
                            continue
                        break
                    st.session_state["_pending_clear"] = keys_to_clear
                    st.rerun()

        if st.button("Clear all triples"):
            st.session_state.triples = []
            st.session_state.id_map = {}
            st.session_state.skipped_paras = set()
            st.session_state.form_version += 1
            st.rerun()
    else:
        st.info("Add questions and answers above to build your JSON export.")


if __name__ == "__main__":
    main()
