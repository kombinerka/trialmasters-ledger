"""
On-demand screen OCR (Alt+F11 by default): reads text from a screenshot the player asks for.

ToS: only runs when you press the hotkey; it reads a normal screenshot of the screen (like any screenshot tool),
never the game's process or memory, and sends nothing to the game. Recognition runs offline on this PC
(RapidOCR / ONNX, `pip install rapidocr_onnxruntime`) - no account or key.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

INSTALL_HINT = "Screen OCR needs two extra packages: .venv\\Scripts\\python.exe -m pip install rapidocr_onnxruntime wordninja"


@dataclass
class OcrLine:
    text: str
    x: int
    y: int
    w: int
    h: int
    score: float = 1.0

    @property
    def center(self) -> Tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_PUNCT_RE = re.compile(r"(?<=[a-z][.,;:!?])(?=[A-Z])")


def fix_spacing(text: str) -> str:
    """OCR sometimes drops spaces ('lessMana', 'exile.The'): put them back."""
    text = _PUNCT_RE.sub(" ", text.strip())
    return _CAMEL_RE.sub(" ", text)


def is_translatable(text: str) -> bool:
    return sum(ch.isalpha() for ch in text) >= 2


class OcrEngine:
    """Lazy wrapper around RapidOCR; `recognize` takes encoded image bytes (PNG) and returns text lines."""

    def __init__(self, backend: Optional[Callable[[bytes], Sequence]] = None, min_score: float = 0.5) -> None:
        self._backend = backend
        self._lock = threading.Lock()
        self.min_score = min_score
        self.error = ""

    def available(self) -> bool:
        return self._load() is not None

    def _load(self) -> Optional[Callable[[bytes], Sequence]]:
        with self._lock:
            if self._backend is None and not self.error:
                try:
                    from rapidocr_onnxruntime import RapidOCR  # type: ignore

                    engine = RapidOCR()
                    self._backend = lambda data: (engine(data)[0] or [])
                except Exception as exc:  # noqa: BLE001 - missing package or broken model download
                    self.error = INSTALL_HINT if isinstance(exc, ImportError) else f"OCR unavailable: {exc}"
                    logger.warning("Screen OCR unavailable: %s", exc)
            return self._backend

    def warm_up(self) -> None:
        """Load the models in the background so the first Alt+F11 is quick."""
        self._load()

    def recognize(self, image_bytes: bytes) -> List[OcrLine]:
        backend = self._load()
        if backend is None:
            raise RuntimeError(self.error or INSTALL_HINT)
        lines: List[OcrLine] = []
        for box, text, score in backend(image_bytes):
            if float(score) < self.min_score or not str(text).strip():
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            lines.append(
                OcrLine(fix_spacing(str(text)), int(min(xs)), int(min(ys)), int(max(xs) - min(xs)), int(max(ys) - min(ys)), float(score))
            )
        lines.sort(key=lambda ln: (ln.y, ln.x))
        return lines


# ---------- turning OCR'd lines into readable text blocks ----------
Box = Tuple[int, int, int, int]  # x, y, w, h

_DIGIT_O_RE = re.compile(r"(?<=[A-Za-z])0(?=[A-Za-z])|(?<=[A-Za-z]{2})0\b|\b0(?=[A-Za-z]{2})")
_NUMERIC_RE = re.compile(r"^[\s\d.,:%/+\-–()x×]*$")


_NUMBER_WITH_O_RE = re.compile(r"(?<![A-Za-z])[0-9Oo]*[0-9][0-9Oo.,]*[0-9Oo](?![A-Za-z]{2})|(?<![A-Za-z])[Oo]\.[0-9]+")
_LONG_WORD_RE = re.compile(r"[A-Za-z]{12,}")
_GAME_WORDS: Optional[set] = None


def _game_words() -> set:
    """Words used in PoE's own passive tree text (names + stats): never split these."""
    global _GAME_WORDS
    if _GAME_WORDS is None:
        import json
        from pathlib import Path

        words: set = set()
        try:
            data = json.loads((Path(__file__).resolve().parent.parent / "data" / "passive_index.json").read_text(encoding="utf-8"))
            entries = data.get("entries", data) if isinstance(data, dict) else data
            for e in entries:
                blob = " ".join([e.get("name", "")] + list(e.get("stats", [])))
                words.update(w.lower() for w in re.findall(r"[A-Za-z]+", blob))
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        _GAME_WORDS = words
    return _GAME_WORDS


def fix_ocr_digits(text: str) -> str:
    """'MINI0NS' -> 'MINIONS' (zero inside a word), '2O.0O' -> '20.00' (letter O inside a number)."""
    text = _DIGIT_O_RE.sub("O", text)
    return _NUMBER_WITH_O_RE.sub(lambda m: m.group(0).replace("O", "0").replace("o", "0"), text)


def _split_words(token: str) -> str:
    """Small caps make OCR drop spaces ('DAMAGEPERSECOND'): split such runs back into English words."""
    try:
        import wordninja  # type: ignore
    except ImportError:
        return token
    model = wordninja.DEFAULT_LANGUAGE_MODEL
    if token.lower() in getattr(model, "_wordcost", {}) or token.lower() in _game_words():
        return token
    parts = model.split(token)
    if len(parts) < 2 or any(len(p) < 2 and p.lower() not in ("a", "i") for p in parts):
        return token
    return " ".join(parts)


def fix_missing_spaces(text: str) -> str:
    return _LONG_WORD_RE.sub(lambda m: _split_words(m.group(0)), text)


def is_numeric(text: str) -> bool:
    return bool(_NUMERIC_RE.match(text))


def normalize_case(text: str) -> str:
    """
    PoE draws many labels in small caps, which OCR reads as ALL CAPS. Shouting text translates badly and is hard
    to read, so it becomes normal sentence case (first letter and letters after . ! ? stay capital).
    """
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 4 or sum(ch.isupper() for ch in letters) < 0.8 * len(letters):
        return text
    out, cap = [], True
    for ch in text.lower():
        if cap and ch.isalpha():
            out.append(ch.upper())
            cap = False
        else:
            out.append(ch)
        if ch in ".!?":
            cap = True
    return "".join(out)


@dataclass
class TextBlock:
    text: str  # what gets translated (one paragraph, normal case)
    boxes: List[Box]  # the original lines it covers
    original: str = ""

    @property
    def bounds(self) -> Box:
        x0 = min(b[0] for b in self.boxes)
        y0 = min(b[1] for b in self.boxes)
        x1 = max(b[0] + b[2] for b in self.boxes)
        y1 = max(b[1] + b[3] for b in self.boxes)
        return x0, y0, x1 - x0, y1 - y0


def _is_caps(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return len(letters) >= 3 and sum(ch.isupper() for ch in letters) >= 0.8 * len(letters)


def _same_row(a: Box, b: Box) -> bool:
    return abs((a[1] + a[3] / 2) - (b[1] + b[3] / 2)) < max(a[3], b[3]) * 0.5


def _join_row_pieces(items: List[Tuple[Box, str]]) -> List[Tuple[Box, str]]:
    """OCR sometimes cuts one line into pieces ('MAIN HAND CRITICAL STRIKE' + 'CHANCE'): glue close neighbours."""
    out: List[Tuple[Box, str]] = []
    for box, text in sorted(items, key=lambda it: (it[0][0])):
        for k, (obox, otext) in enumerate(out):
            gap = box[0] - (obox[0] + obox[2])
            if (_same_row(obox, box) and -4 <= gap < 1.5 * max(obox[3], box[3])
                    and not is_numeric(text) and not is_numeric(otext)):
                x0, y0 = obox[0], min(obox[1], box[1])
                x1, y1 = box[0] + box[2], max(obox[1] + obox[3], box[1] + box[3])
                out[k] = ((x0, y0, x1 - x0, y1 - y0), f"{otext} {text}")
                break
        else:
            out.append((box, text))
    return sorted(out, key=lambda it: (it[0][1], it[0][0]))


def build_blocks(lines: Sequence[Tuple[Box, str]], merge: bool = True) -> List[TextBlock]:
    """
    Joins wrapped lines of one paragraph (a gem description, quest text) so they are translated as one sentence,
    while table rows (character sheet: label + value on the same row) stay separate.
    """
    items = sorted(((b, fix_missing_spaces(fix_ocr_digits(t.strip()))) for b, t in lines if t.strip()),
                   key=lambda it: (it[0][1], it[0][0]))
    items = _join_row_pieces(items)
    has_value_beside = [
        any(j != i and is_numeric(t2) and _same_row(b, b2) for j, (b2, t2) in enumerate(items)) for i, (b, _) in enumerate(items)
    ]
    blocks: List[TextBlock] = []
    used = [False] * len(items)
    for i, (box, text) in enumerate(items):
        if used[i]:
            continue
        used[i] = True
        block = TextBlock(text=text, boxes=[box], original=text)
        blocks.append(block)
        if not merge or is_numeric(text) or has_value_beside[i]:
            continue
        last_box, last_text = box, text
        for j in range(i + 1, len(items)):
            if used[j]:
                continue
            nbox, ntext = items[j]
            gap = nbox[1] - (last_box[1] + last_box[3])
            if gap > last_box[3] * 0.9:
                break
            if gap < -last_box[3] * 0.5 or is_numeric(ntext) or has_value_beside[j]:
                continue
            if any(ch.isdigit() for ch in last_text) and any(ch.isdigit() for ch in ntext):
                break  # two stat lines ("+48% to ...", "11073 maximum Life"), not a wrapped sentence
            overlap = min(last_box[0] + last_box[2], nbox[0] + nbox[2]) - max(last_box[0], nbox[0])
            wrapped = (
                overlap > 0.5 * min(last_box[2], nbox[2])
                and not last_text.rstrip().endswith((".", "!", "?", ":"))
                and (ntext[:1].islower() or (len(last_text) >= 30 and _is_caps(last_text) and _is_caps(ntext)))
                and last_box[2] >= 0.6 * max(last_box[2], nbox[2])
            )
            if not wrapped:
                break
            used[j] = True
            block.boxes.append(nbox)
            block.text = f"{block.text} {ntext}"
            block.original = f"{block.original}\n{ntext}"
            last_box, last_text = nbox, ntext
    for b in blocks:
        b.text = normalize_case(b.text)
    return blocks


def detect_alignment(boxes: Sequence[Box]) -> str:
    """'center' for centred tooltips (gems, passives), 'left' for lists (character sheet, quest tracker)."""
    if len(boxes) < 3:
        return "center"
    lefts = sorted(b[0] for b in boxes)
    centers = sorted(b[0] + b[2] / 2 for b in boxes)
    spread = lambda vals: vals[int(len(vals) * 0.8)] - vals[int(len(vals) * 0.2)]  # noqa: E731
    return "left" if spread(lefts) < spread(centers) else "center"
