"""Read the order image into an Order: a vision LLM reads it, OCR double-checks it.

- The LLM (Google Gemini, free tier) understands the layout and returns JSON
  that must match the Order schema in schema.py.
- The result is saved to a JSON file, so later runs skip the LLM entirely.
- Witness check: every text value the LLM returned must also appear,
  roughly, in what Windows OCR reads from the same image. That catches
  text the LLM invented. Numbers are checked by validate.py instead.
"""
import logging
import os
import time
from difflib import SequenceMatcher
from pathlib import Path

from .errors import ManualReview
from .ocr import read_text
from .schema import Order

log = logging.getLogger("image_to_cash")


# ---------- the LLM call ----------

# Free-tier models, tried in this order until one answers. Each model has its
# own capacity and quota, so when one is overloaded (503) or rate-limited (429),
# another usually isn't. Measured on 2026-11-21: all three read samples/order.png
# exactly like the human reading (samples/order.expected.json); the "lite" model
# answers in about 3 s, the other two in about 17 s. The newest models (3.7, 3.8)
# answered 503 or hung every time, so they are not used.
# GEMINI_MODEL in .env picks one model instead.
MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash"]
UNAVAILABLE = {404, 429, 500, 503, 504}  # model gone, rate-limited, overloaded: try the next one
REQUEST_TIMEOUT_MS = 20_000  # no answer within 20 s counts as unavailable: try the next one

PROMPT = """You are reading a sales order document.
Extract every value into the JSON schema you were given.
Rules:
- Copy text exactly as printed (same spelling, capitals and punctuation).
- Numbers as plain numbers: 250.00 not "EUR 250.00", 10 for "10%".
- Dates as YYYY-MM-DD.
- Split the contact person's name into first_name and last_name.
- If a value is not printed or not readable, use null. Never guess or invent.
"""


def extract_with_llm(image_path: Path) -> Order:
    """Image + instructions in, schema-checked Order out.

    Fallback: the models in MODELS are tried one after another. If none of
    them answers, the run stops for manual review (fast, about a minute at
    most): a saved reading (--from-json) still works.
    """
    from google import genai  # imported here so --from-json runs need no Gemini setup
    from google.genai import errors, types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ManualReview("GEMINI_API_KEY is not set (put it in .env), or use --from-json")
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    chosen = os.environ.get("GEMINI_MODEL")
    models = [chosen] if chosen else MODELS
    image = types.Part.from_bytes(data=Path(image_path).read_bytes(), mime_type="image/png")
    for model in models:
        log.info("Asking %s to read the image...", model)
        start = time.time()
        try:
            response = client.models.generate_content(
                model=model,
                contents=[image, PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=Order,   # Gemini must answer with exactly these fields
                    temperature=0,           # as repeatable as possible
                ),
            )
        except errors.APIError as error:
            if error.code not in UNAVAILABLE:  # e.g. a wrong key: no point trying other models
                raise ManualReview(f"the Gemini call failed: {error}") from error
            log.warning("%s is unavailable (%s %s): trying the next model", model, error.code, error.status)
            continue
        except Exception as error:  # no internet, timeout
            log.warning("%s could not be reached (%s): trying the next model", model, error)
            continue
        if response.parsed is None:
            raise ManualReview(f"the LLM answer did not match the schema: {response.text!r}")
        log.info("Image read by %s in %.0f s", model, time.time() - start)
        return response.parsed
    raise ManualReview("no Gemini model answered: try again later, use --from-json with a saved "
                       "reading, or write the JSON by hand from the image")


# ---------- the OCR witness ----------

# A value "appears" in the OCR text if some part of it is at least this similar.
# Below that, OCR saw nothing like it: the LLM probably made it up.
# Measured on samples/order.png: true values score 0.80-1.00, an invented
# street 0.56. A one-letter misread still scores ~0.8, so this catches
# invented text, not typos (the exact SKU match in Fakturama covers those).
WITNESS_THRESHOLD = 0.7


def similarity(value: str, ocr_text: str) -> float:
    """How well `value` appears somewhere in the OCR text: 1.0 = exactly, 0 = not at all.

    OCR lines often hold more than one value ("10117 Berlin"), so the value is
    compared with every run of the same number of words, on every line.
    """
    value_words = value.lower().split()
    best = 0.0
    for line in ocr_text.lower().splitlines():
        words = line.split()
        size = len(value_words)
        for start in range(max(1, len(words) - size + 1)):
            window = " ".join(words[start:start + size])
            best = max(best, SequenceMatcher(None, " ".join(value_words), window).ratio())
    return best


def text_values(order: Order) -> dict[str, str]:
    """Every printed text value of the order, by a readable name."""
    values = {
        "external reference": order.external_reference,
        "order date": order.order_date.isoformat(),
        "company": order.debtor.company,
        "alias": order.debtor.alias,
        "contact name": f"{order.debtor.first_name} {order.debtor.last_name}",
        "email": order.debtor.email,
        "phone": order.debtor.phone,
        "payment method": order.payment.method,
        "payment date": order.payment.date.isoformat() if order.payment.date else None,
    }
    for label, address in [("billing", order.billing_address), ("delivery", order.delivery_address)]:
        values[f"{label} name"] = address.name
        values[f"{label} street"] = address.street
        values[f"{label} ZIP + city"] = f"{address.zip} {address.city}"
        values[f"{label} country"] = address.country
    for number, item in enumerate(order.items, start=1):
        values[f"item {number} SKU"] = item.sku
        values[f"item {number} description"] = item.description
    return {name: value for name, value in values.items() if value}


def witness_scores(order: Order, image_path: Path) -> dict[str, float]:
    """Similarity score of every text value against OCR of the same image."""
    ocr_text = read_text(image_path)
    return {name: similarity(value, ocr_text) for name, value in text_values(order).items()}


def check_witness(order: Order, image_path: Path) -> dict[str, float]:
    """Stop if OCR saw nothing like one of the LLM's values. Returns all scores for the log."""
    scores = witness_scores(order, image_path)
    missing = [f"{name} ({score:.2f})" for name, score in scores.items() if score < WITNESS_THRESHOLD]
    if missing:
        raise ManualReview("OCR could not confirm these values from the image: " + ", ".join(missing))
    return scores


# ---------- cache: reuse a saved reading ----------

def load_or_extract(image_path: Path, json_path: Path, use_cached: bool) -> Order:
    """Use the saved JSON if asked to; otherwise call the LLM and save its answer."""
    if use_cached:
        if not json_path.exists():
            raise ManualReview(f"--from-json given, but {json_path} does not exist")
        return Order.model_validate_json(json_path.read_text(encoding="utf-8"))
    order = extract_with_llm(image_path)
    json_path.write_text(order.model_dump_json(indent=2), encoding="utf-8", newline="\n")  # LF, like the repo
    return order
