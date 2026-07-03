"""Normalize place names to: Romaji (Original) when Japanese script is present."""
import re

import pykakasi

KKS = pykakasi.kakasi()

JP_CHAR = r"[\u3040-\u309F\u30A0-\u30FA\u30FC-\u30FF\u4E00-\u9FFF\u3400-\u4DBF\uFF66-\uFF9F]"
JP_RE = re.compile(JP_CHAR)
LATIN_RE = re.compile(r"[A-Za-z]")


def _has_japanese(text: str) -> bool:
    return bool(JP_RE.search(text))


def _clean_formatting(text: str) -> str:
    text = text.replace("**", "")
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("［", "[").replace("］", "]")
    text = text.replace("　", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_japanese_original(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        if JP_RE.match(ch) or ch in "（）()[]・&、":
            chars.append(ch)
        elif ch.isspace() and chars and chars[-1] != " ":
            chars.append(" ")
    result = "".join(chars)
    result = re.sub(r"\s+", " ", result).strip(" -.、")
    # Unwrap single parenthetical layer: (カナ) -> カナ
    m = re.fullmatch(r"\((.+)\)", result)
    if m and not LATIN_RE.search(m.group(1)):
        result = m.group(1).strip()
    return result


def _to_romaji(japanese: str) -> str:
    source = re.sub(r"[()（）\[\]]", " ", japanese)
    source = re.sub(r"\s+", " ", source).strip()
    pieces = []
    for part in KKS.convert(source):
        hepburn = part.get("hepburn", "").strip()
        if hepburn:
            pieces.append(hepburn.title())
    romaji = " ".join(pieces)
    return re.sub(r"\s+", " ", romaji).strip()


def _strip_annotations(text: str) -> str:
    m = re.search(r"\s+[-–—]\s+(.+)$", text)
    if m:
        tail = m.group(1).strip()
        if tail and not re.search(r"[A-Z0-9]", tail) and not _has_japanese(tail):
            text = text[: m.start()].rstrip()

    m = re.search(r"\s+・\s+(.+)$", text)
    if m:
        tail = m.group(1).strip()
        if tail and not _has_japanese(tail):
            text = text[: m.start()].rstrip()

    return text.strip()


def _clean_latin(text: str) -> str:
    text = text.replace("＆", "&")
    text = _strip_annotations(text)
    text = re.sub(r"\s+-\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .,-")
    return text


def _latin_chunks(name: str) -> list[str]:
    parts = re.split(f"({JP_CHAR}+)", name)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if _has_japanese(part):
            if len(part.strip()) <= 3:
                continue
            if buf.strip() and LATIN_RE.search(buf):
                chunks.append(_clean_latin(buf))
            buf = ""
        else:
            buf += part
    if buf.strip() and LATIN_RE.search(buf):
        chunks.append(_clean_latin(buf))
    return [c for c in chunks if c]


def _meaningful_latin_chunks(name: str) -> list[str]:
    chunks = _latin_chunks(name)
    return [c for c in chunks if re.search(r"[A-Z]", c) or len(c.split()) >= 2]


def _latin_before_japanese(name: str) -> str | None:
    m = JP_RE.search(name)
    if not m:
        return None
    before = _clean_latin(name[: m.start()])
    return before if before and LATIN_RE.search(before) else None


def _latin_after_japanese(name: str) -> str | None:
    m = re.search(rf"({JP_CHAR}[\s()（）\[\]・&、\-]*)+\s+([A-Za-z].+)$", name)
    if not m:
        return None
    tail = _clean_latin(m.group(2))
    return tail if tail and LATIN_RE.search(tail) else None


def _latin_primary(name: str, japanese_original: str) -> str | None:
    m = re.match(rf"^({JP_CHAR}+\s*)+\(([^)]+)\)\s*$", name)
    if m and not _has_japanese(m.group(2)):
        return _clean_latin(m.group(2))

    m = re.match(rf"^(.+?)\s*\(({JP_CHAR}+)\)\s*$", name)
    if m and LATIN_RE.search(m.group(1)):
        return _clean_latin(m.group(1))

    m = re.match(rf"^(.+?)\s*\[({JP_CHAR}+)\]\s*$", name)
    if m and LATIN_RE.search(m.group(1)):
        return _clean_latin(m.group(1))

    m = re.match(rf"^([A-Za-z][A-Za-z0-9]*)({JP_CHAR}.+)$", name)
    if m:
        return _clean_latin(f"{m.group(1)} {_to_romaji(japanese_original)}")

    m = re.match(rf"^([A-Za-z0-9][A-Za-z0-9.\-]*)({JP_CHAR}+)\s*$", name)
    if m:
        return _clean_latin(f"{m.group(1)} {_to_romaji(m.group(2))}")

    m = re.match(rf"^({JP_CHAR}+)([A-Za-z][A-Za-z0-9]*)$", name)
    if m:
        return _clean_latin(f"{_to_romaji(m.group(1))} {m.group(2)}")

    if JP_RE.match(name):
        after = _latin_after_japanese(name)
        if after:
            return after
        embeds = re.findall(r"[A-Za-z][A-Za-z0-9]*", name)
        if len(embeds) == 1:
            return _clean_latin(f"{embeds[0]} {_to_romaji(japanese_original)}")
        m = re.match(rf"^([A-Za-z][A-Za-z0-9]*)\s*({JP_CHAR})", name)
        if m:
            return _clean_latin(f"{m.group(1)} {_to_romaji(japanese_original)}")
        return None

    chunks = _meaningful_latin_chunks(name)
    if chunks:
        return max(chunks, key=lambda c: (len(c), c.count(" "), c.count("-")))

    before = _latin_before_japanese(name)
    if before:
        return before

    m = re.match(rf"^([A-Za-z][A-Za-z0-9]*)\s*({JP_CHAR})", name)
    if m:
        return _clean_latin(f"{m.group(1)} {_to_romaji(japanese_original)}")

    return None


def normalize_group_name(name: str) -> str:
    """Simplify source labels: drop [N] prefix and trailing - {kanji/kana}."""
    name = name.strip()
    name = re.sub(r"^\[\d+\]\s*", "", name)
    name = re.sub(r"\s*-\s*[" + JP_CHAR[1:-1] + r"\s]+$", "", name)
    return name.strip()


def normalize_place_name(name: str) -> str:
    name = _clean_formatting(name)
    if not name or not _has_japanese(name):
        return name

    japanese_original = _extract_japanese_original(name)
    latin = _latin_primary(name, japanese_original)
    romaji = latin if latin else _to_romaji(japanese_original)

    if japanese_original:
        return f"{romaji} ({japanese_original})"
    return romaji