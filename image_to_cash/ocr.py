"""Windows' built-in OCR engine (Windows.Media.Ocr), called from Python.

Nothing to install besides the `winrt-*` Python packages: the engine ships
with Windows 10/11. It is used for two things:
- the "witness" check on the LLM's extraction (extract.py)
- the "does this table have rows?" check in Fakturama (ui.py)
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import (
    BitmapAlphaMode,
    BitmapDecoder,
    BitmapInterpolationMode,
    BitmapPixelFormat,
    BitmapTransform,
    ColorManagementMode,
    ExifOrientationMode,
)
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

# Windows OCR misses small text. Images narrower than this are scaled up first.
MIN_WIDTH = 2000


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


def read_lines(image_path: Path) -> list[OcrLine]:
    """Return every text line Windows OCR finds in a PNG/JPG, top to bottom.

    Runs in a separate thread: the main thread belongs to UI Automation
    (a single-threaded COM apartment), and WinRT's async calls deadlock there.
    """
    image_bytes = Path(image_path).read_bytes()
    with ThreadPoolExecutor(max_workers=1) as worker:
        return worker.submit(asyncio.run, _read_lines(image_bytes)).result(timeout=30)


def read_text(image_path: Path) -> str:
    """All recognized text as one string, one line per OCR line."""
    return "\n".join(line.text for line in read_lines(image_path))


async def _read_lines(image_bytes: bytes) -> list[OcrLine]:
    # 1. Put the file's bytes into an in-memory stream that WinRT can read.
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(image_bytes)
    await writer.store_async()
    writer.detach_stream()
    stream.seek(0)

    # 2. Decode the PNG/JPG into a bitmap, scaling small images up so that
    #    small print becomes readable for the engine.
    decoder = await BitmapDecoder.create_async(stream)
    scale = max(1, MIN_WIDTH / decoder.pixel_width)
    transform = BitmapTransform()
    transform.scaled_width = round(decoder.pixel_width * scale)
    transform.scaled_height = round(decoder.pixel_height * scale)
    transform.interpolation_mode = BitmapInterpolationMode.FANT
    bitmap = await decoder.get_software_bitmap_transformed_async(
        BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED, transform,
        ExifOrientationMode.IGNORE_EXIF_ORIENTATION, ColorManagementMode.DO_NOT_COLOR_MANAGE)

    # 3. Recognize English text.
    engine = OcrEngine.try_create_from_language(Language("en-US"))
    if engine is None:
        raise RuntimeError("Windows OCR for en-US is not installed")
    result = await engine.recognize_async(bitmap)

    lines = []
    for line in result.lines:
        boxes = [word.bounding_rect for word in line.words]
        top = min(b.y for b in boxes)
        lines.append(OcrLine(  # positions converted back to the original image's pixels
            text=line.text,
            x=min(b.x for b in boxes) / scale,
            y=top / scale,
            height=(max(b.y + b.height for b in boxes) - top) / scale,
            words=[OcrWord(w.text, w.bounding_rect.x / scale, w.bounding_rect.y / scale,
                           w.bounding_rect.width / scale, w.bounding_rect.height / scale)
                   for w in line.words],
        ))
    return sorted(lines, key=lambda line: (line.y, line.x))
