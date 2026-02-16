# PDF QA Builder

A Streamlit app to build SQuAD-style QA datasets from PDF documents. Upload a PDF, parse into paragraphs, add Q&A pairs (including unanswerable questions), and export to JSON.

## Features

- **PDF or Markdown upload**: Docling converts PDF→markdown; ACS or Generic parser; project chunker for section/paragraph splits
- **Parser selection**: Choose ACS (for American Chemical Society papers) or Generic in the sidebar
- **Section filtering**: Excludes References, Figures/Tables, Preamble; includes all other sections
- **Unicode fixes**: Decodes `/uniXXXX` Macdown placeholders (e.g. `/uniFB01` → ﬁ) in parsed text
- **Manual Q&A entry**: Add questions and answers per paragraph; multiple QA pairs per paragraph
- **Unanswerable questions**: Check "No answer in context" for questions that cannot be answered from the text
- **SQuAD-style export**: `is_impossible`, `answers: [{"text": "...", "answer_start": n}]`
- **Suggest answer**: Optional LLM extraction of answer span from context (requires OpenAI API key)
- **Progress markers**: Sidebar shows questions count and paragraphs without QA
- **Skip paragraph**: Mark paragraphs as "nothing useful" to hide them
- **Filter**: Show only paragraphs without QA to focus on remaining work
- **Delete QA pair**: Remove a specific QA pair by its index (0-based) before export

## Setup

```bash
cd QA-app
pip install -r requirements.txt
```

### Dependencies

- **streamlit**: Web UI
- **docling**: PDF→markdown conversion
- **openai**: For "Suggest answer" feature (optional if no API key)
- **lxml, pandas, unidecode, textacy**: ACS markdown parser
- **pylogg** (optional): Stub used if not installed

### Optional: OpenAI for "Suggest answer"

Create `.streamlit/secrets.toml`:

```toml
OPENAI_API_KEY = "sk-your-key-here"
```

## Run

```bash
streamlit run app.py
```

## Output format (SQuAD-style)

```json
[
  {
    "id": "uuid",
    "context": "The paragraph text...",
    "question": "What is X?",
    "is_impossible": false,
    "answers": [{"text": "exact span", "answer_start": 42}],
    "section": "INTRODUCTION"
  },
  {
    "id": "uuid",
    "context": "...",
    "question": "What about Y?",
    "is_impossible": true,
    "answers": []
  }
]
```

- `answer_start`: Character offset of the answer in context, or `-1` if not an exact span
- `is_impossible`: `true` when the question cannot be answered from the context
