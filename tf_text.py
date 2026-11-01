"""Vietnamese text helpers: normalisation, sentence/scene segmentation, hashing."""
from __future__ import annotations

import hashlib
import re
import unicodedata

ABBREVIATIONS = {'tp', 'tt', 'ths', 'ts', 'pgs', 'gs', 'bs', 'mr', 'mrs', 'ms', 'dr', 'st', 'jr',
                 'inc', 'ltd', 'co', 'vs', 'no', 'u', 'v.v', 'ubnd', 'hlv'}


def normalize(text: str) -> str:
    """Lower-case, strip diacritics (đ→d), keep [a-z0-9 ] only."""
    s = unicodedata.normalize('NFD', str(text or '').lower())
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn').replace('đ', 'd')
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return ' '.join(s.split())


def clean_spaces(text: str) -> str:
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def word_count(text: str) -> int:
    return len(clean_spaces(text).split()) if clean_spaces(text) else 0


def sha(text: str) -> str:
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


def narration_hash(narration: str) -> str:
    return sha(clean_spaces(narration))


def split_sentences(text: str) -> list[str]:
    """Split at . ! ? … when followed by whitespace; protects abbreviations and decimals."""
    text = clean_spaces(text)
    if not text:
        return []
    tokens = text.split(' ')
    sentences, current = [], []
    for i, tok in enumerate(tokens):
        current.append(tok)
        stripped = tok.rstrip('"”’\')»')
        if not re.search(r'([.!?…]|\.\.\.)$', stripped):
            continue
        base = re.sub(r'[.!?…]+$', '', stripped).lower()
        if stripped.endswith('.') and (base in ABBREVIATIONS or re.fullmatch(r'[a-zà-ỹ]', base)):
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ''
        first = nxt.lstrip('"“‘(«')[:1]
        if nxt and not (first.isupper() or first.isdigit()):
            continue
        sentences.append(' '.join(current))
        current = []
    if current:
        sentences.append(' '.join(current))
    return sentences


def _split_long(sentence: str, max_words: int) -> list[str]:
    words = sentence.split()
    if len(words) <= max_words:
        return [sentence]
    # Prefer a comma near the middle.
    mid = len(words) // 2
    best = None
    for j in range(4, len(words) - 4):
        if re.search(r'[,;:]$', words[j - 1]):
            if best is None or abs(j - mid) < abs(best - mid):
                best = j
    cut = best or mid
    return _split_long(' '.join(words[:cut]), max_words) + _split_long(' '.join(words[cut:]), max_words)


def scene_segments(narration: str, min_words: int = 6, max_words: int = 30) -> list[str]:
    """Scenes follow spoken sentences exactly. Every narration word is in exactly one scene."""
    parts = []
    for s in split_sentences(narration):
        parts.extend(_split_long(s, max_words))
    merged: list[str] = []
    for p in parts:
        if merged and (word_count(p) < min_words or word_count(merged[-1]) < min_words) \
                and word_count(merged[-1]) + word_count(p) <= max_words + 6:
            merged[-1] = merged[-1] + ' ' + p
        else:
            merged.append(p)
    assert ' '.join(merged).split() == clean_spaces(narration).split()
    return merged


def numbers_in(text: str) -> set[str]:
    """Numbers as they appear (normalised separators), e.g. '1.200' -> '1200', '3,5' -> '3.5'."""
    out = set()
    for m in re.findall(r'\d+(?:[.,]\d+)*', str(text or '')):
        if re.fullmatch(r'\d{1,3}(?:\.\d{3})+', m):
            out.add(m.replace('.', ''))
        elif re.fullmatch(r'\d{1,3}(?:,\d{3})+', m):
            out.add(m.replace(',', ''))
        else:
            out.add(m.replace(',', '.'))
    return out


SENTENCE_STARTERS = set('''theo trong day do ngay sau tai khi voi cac nhung mot ong ba anh chi nhung
nam thang hom vao tu den neu nhu va nhung con cung chinh ca moi hien truoc gan rat the ke luc ngoai
nay chua da dang se vi nen tuy du bang qua tren duoi ben giua phia this the a an in on at for after
before when with as by from it its their there these those but and or however meanwhile'''.split())


def proper_names(text: str, known: set | None = None) -> list[str]:
    """Heuristic proper-name extraction for Vietnamese/English text (capitalised runs).
    `known`: normalised names seen mid-sentence elsewhere, so they are kept at sentence start."""
    names, seen = [], set()
    for sentence in split_sentences(text):
        # A comma/colon/bracket ends a name: "PUBG, Donald Glover" is two names, not one.
        words = re.findall(r"[\wÀ-ỹ][\wÀ-ỹ'’.-]*|[,;:()\[\]\"“”–—]", sentence)
        run: list[str] = []

        def flush(at_start=False):
            if not run:
                return
            phrase = ' '.join(run).strip(".-'’")
            key = normalize(phrase)
            # A lone capitalised first word is just sentence case ("Kính", "Giá"), not a name.
            w = run[0] if run else ''
            special = len(w) >= 2 and (w.isupper() or any(ch.isupper() for ch in w[1:]))  # NASA, SpaceX
            if len(run) == 1 and at_start and not special and key not in (known or ()):
                run.clear()
                return
            if len(key) >= 3 and key not in seen and not (len(run) == 1 and key in SENTENCE_STARTERS):
                seen.add(key)
                names.append(phrase)
            run.clear()

        run_start = 0
        for idx, w in enumerate(words):
            if not (w[:1].isalnum()):
                flush(run_start == 0)
                continue
            is_cap = w[:1].isupper() or (w[:1].isdigit() and run)
            if is_cap and not (idx == 0 and normalize(w) in SENTENCE_STARTERS):
                if not run:
                    run_start = idx
                run.append(w)
            else:
                flush(run_start == 0)
        flush(run_start == 0)
    return names
