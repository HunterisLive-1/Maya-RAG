"""Ingest engineering PDFs into BoilerMind ChromaDB (run separately)."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from pathlib import Path
from typing import List, Tuple

import fitz

from book_rag import book_rag
from paths import books_dir, data_dir

logger = logging.getLogger("boilermind-ingest")

TARGET_WORDS = 500
OVERLAP_WORDS = 50
BLANK_PAGE_CHARS = 50


def _words_split(text: str) -> List[str]:
    parts = text.split()
    return [w for w in parts if w]


def _chunks_from_words(
    words: List[str],
    target: int,
    overlap: int,
) -> List[Tuple[int, int]]:
    """Indices [start,end) slice bounds on ``words`` for each chunk."""
    if not words:
        return []
    spans: List[Tuple[int, int]] = []
    i = 0
    while i < len(words):
        end = min(i + target, len(words))
        spans.append((i, end))
        if end >= len(words):
            break
        i = max(0, end - overlap)
        if i <= spans[-1][0]:
            i = spans[-1][1]
    return spans


def _page_word_boundaries(words_per_page: List[List[str]]) -> List[int]:
    """Cumulative word starts per page (global word index where page begins)."""
    bounds = []
    cum = 0
    for w in words_per_page:
        bounds.append(cum)
        cum += len(w)
    return bounds


def build_chunks_for_pdf_pages(page_texts: List[str]) -> List[Tuple[str, int]]:
    """Return list of (chunk_text, page_1_based)."""
    per_page_words: List[List[str]] = []
    for txt in page_texts:
        per_page_words.append(_words_split(txt))

    cum_bounds = _page_word_boundaries(per_page_words)
    flat: List[str] = []
    for w in per_page_words:
        flat.extend(w)

    spans = _chunks_from_words(flat, TARGET_WORDS, OVERLAP_WORDS)
    out: List[Tuple[str, int]] = []
    for start, end in spans:
        piece = flat[start:end]
        if not piece:
            continue
        chunk_text = " ".join(piece)
        middle = (start + end) // 2
        idx = middle
        pg = 1
        for p_i in range(len(cum_bounds) - 1, -1, -1):
            if cum_bounds[p_i] <= idx:
                pg = p_i + 1
                break
        out.append((chunk_text, pg))
    return out


def ingest_pdf(
    pdf_path: str,
    book_name: str,
    book_id: str,
    *,
    on_page: Callable[[int, int], None] | None = None,
    print_progress: bool = True,
    force: bool = False,
) -> int:
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(pdf_path)

    if not book_rag.connect():
        if print_progress:
            print("Cannot connect book_rag.")
        return 0

    col = book_rag._collection

    # Check if already fully ingested (only skip if complete AND not forced).
    existing = col.get(where={"book_id": book_id}, limit=1)
    if existing.get("ids"):
        if force:
            # Delete stale/partial entry and re-ingest fresh.
            col.delete(where={"book_id": book_id})
            if print_progress:
                print(f"Force re-ingest: deleted existing entry for '{book_name}'.")
        else:
            if print_progress:
                print(f"'{book_name}' already ingested, skipping.")
            return 0

    doc = fitz.open(path)
    try:
        n_pages = len(doc)
        page_texts = []
        for i in range(n_pages):
            t = doc.load_page(i).get_text()
            stripped = (t or "").strip()
            if len(stripped) < BLANK_PAGE_CHARS:
                page_texts.append("")
            else:
                page_texts.append(t)
            cur = i + 1
            if on_page is not None:
                try:
                    on_page(cur, n_pages)
                except Exception:
                    pass
            if print_progress and (cur % 25 == 0 or cur == n_pages):
                print(f"Page {cur}/{n_pages} read...")
        pairs = build_chunks_for_pdf_pages(page_texts)
        if not pairs:
            if print_progress:
                print("No textual content extracted; skipping.")
            return 0

        texts = []
        metas = []
        ids_out = []
        for idx, (text, pg) in enumerate(pairs):
            texts.append(text)
            metas.append(
                {
                    "book_name": book_name,
                    "book_id": book_id,
                    "page": int(pg),
                    "chunk_index": int(idx),
                }
            )
            ids_out.append(f"{book_id}:{idx}")

        batch = 250
        total_added = 0
        for b in range(0, len(texts), batch):
            sl = slice(b, b + batch)
            col.add(
                ids=ids_out[sl],
                documents=texts[sl],
                metadatas=metas[sl],
            )
            total_added += len(texts[sl])
            logger.info(
                "ingest_pdf: '%s' batch %d/%d — %d/%d chunks added",
                book_name,
                b // batch + 1,
                -(-len(texts) // batch),
                total_added,
                len(texts),
            )

        if print_progress:
            print(f"Ingested {total_added} chunks for '{book_name}'.")
        return total_added
    finally:
        doc.close()



def scan_books_folder(folder: str | Path | None = None) -> List[dict]:
    root = Path(folder) if folder is not None else books_dir()
    found = []
    for p in sorted(root.rglob("*.pdf")):
        name = p.stem
        book_id = name.replace(" ", "_").lower()
        found.append({"path": str(p), "book_name": name, "book_id": book_id})
    return found


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    ap = argparse.ArgumentParser(description="BoilerMind PDF ingestion")
    ap.add_argument("--book", type=str, default=None)
    ap.add_argument("--name", type=str, default=None)
    args = ap.parse_args()

    books_dir().mkdir(parents=True, exist_ok=True)
    data_dir()

    total_chunks = 0
    books_done = 0

    try:
        if args.book:
            p = Path(args.book)
            book_name = args.name if args.name else p.stem
            book_id = p.stem.replace(" ", "_").lower()
            n = ingest_pdf(str(p.resolve()), book_name, book_id)
            total_chunks += n
            if n > 0:
                books_done += 1
        else:
            items = scan_books_folder()
            if not items:
                print(
                    'No PDFs found in books/ folder. Drop .pdf files in "books/" '
                    "or pass --book path/to/file.pdf"
                )
                return
            for it in items:
                n = ingest_pdf(it["path"], it["book_name"], it["book_id"])
                total_chunks += n
                if n > 0:
                    books_done += 1
    except FileNotFoundError as e:
        print(f"File not found: {e}")

    print(
        f"Ingestion complete. {total_chunks} chunks added across {books_done} book(s)."
    )


if __name__ == "__main__":
    main()
