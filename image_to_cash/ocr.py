"""OCR with RapidOCR: PaddleOCR's models, run on the CPU through ONNX Runtime.

The models ship inside the `rapidocr_onnxruntime` package, so it works offline
and downloads nothing at run time. It is used for:
- the "witness" check on the LLM's extraction (extract.py)
- Fakturama's custom-drawn tables: "any rows?" and "where to click" (ui.py)

It replaced Windows' built-in OCR, which couldn't read Fakturama's faint table
headers or a noisy fax.
"""
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from statistics import median

from rapidocr_onnxruntime import RapidOCR
from rapidocr_onnxruntime.utils.process_img import ResizeImgError


@dataclass
class OcrWord:
    text: str
    x: float       # left edge in image pixels
    y: float       # top edge in image pixels
    width: float
    height: float


@dataclass
class OcrLine:
    text: str
    x: float       # left edge in image pixels
    y: float       # top edge in image pixels
    height: float
    words: list[OcrWord]


@cache
def _engine() -> RapidOCR:
    """Load the models once (about a second), then reuse them."""
    return RapidOCR()


def read_lines(image_path: Path) -> list[OcrLine]:
    """Every piece of text RapidOCR finds in a PNG/JPG, top to bottom, with a box per word."""
    try:
        result, _elapsed = _engine()(str(image_path), return_word_box=True)
    except ResizeImgError:  # a strip a few pixels high: too thin to hold any text
        return []
    lines = []
    for box, text, _score, char_boxes, chars, _char_scores in result or []:
        top = min(point[1] for point in box)
        height = max(point[1] for point in box) - top
        left = min(point[0] for point in box)
        if char_boxes:
            words = _words(chars, char_boxes, top, height)
        else:  # no character positions: the whole text is one word
            words = [OcrWord(text, left, top, max(point[0] for point in box) - left, height)]
        lines.append(OcrLine(text, left, top, height, words))
    return sorted(lines, key=lambda line: (line.y, line.x))


def read_text(image_path: Path) -> str:
    """All recognized text as one string, one line per text box, words split by spaces."""
    return "\n".join(" ".join(word.text for word in line.words) for line in read_lines(image_path))


def _words(chars: list[str], char_boxes: list, top: float, height: float) -> list[OcrWord]:
    """Group the characters into words. A word ends at a space, or at a gap wider
    than 1.6 characters: RapidOCR sometimes reads two neighbouring table cells as
    one text ("2.00LMP-LED-05"), and the gap between the cells splits them again.
    Measured: gaps inside words reach 1.3 characters, the gap between cells 1.9."""
    spans = [(min(p[0] for p in box), max(p[0] for p in box)) for box in char_boxes]
    char_width = median(right - left for left, right in spans)
    words, current, previous_right = [], [], None
    for char, (left, right) in zip(chars, spans):
        if current and (char == " " or left - previous_right > 1.6 * char_width):
            words.append(current)
            current = []
        if char != " ":
            current.append((char, left, right))
        previous_right = right
    if current:
        words.append(current)
    return [OcrWord("".join(c for c, _, _ in word), word[0][1], top, word[-1][2] - word[0][1], height)
            for word in words]
