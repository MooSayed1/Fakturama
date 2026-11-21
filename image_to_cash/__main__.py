"""Command line entry point: read -> check -> drive Fakturama -> verify.

    uv run python -m image_to_cash samples/order.png                 # LLM reads the image
    uv run python -m image_to_cash samples/order.png --from-json     # reuse the saved reading
    uv run python -m image_to_cash samples/order.png --extract-only  # stop before Fakturama
    uv run python -m image_to_cash samples/order.png --slow          # slow motion, to watch it

Exit codes: 0 = done and verified, 2 = stopped for manual review.
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from .errors import ManualReview

log = logging.getLogger("image_to_cash")


def main() -> int:
    parser = argparse.ArgumentParser(prog="image_to_cash", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path, help="the order image (PNG)")
    parser.add_argument("--json", type=Path,
                        help="where the extracted order is saved/read (default: <image>.extracted.json)")
    parser.add_argument("--from-json", action="store_true",
                        help="use the saved JSON instead of calling the LLM")
    parser.add_argument("--extract-only", action="store_true",
                        help="extract and validate, but don't touch Fakturama")
    parser.add_argument("--slow", type=float, nargs="?", const=1.0, default=0.0, metavar="SECONDS",
                        help="slow motion: wait after every action in Fakturama (default 1 second)")
    args = parser.parse_args()
    json_path = args.json or args.image.with_suffix(".extracted.json")

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # GEMINI_API_KEY, from the project folder
    run_dir = Path("runs") / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir.mkdir(parents=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(run_dir / "run.log", encoding="utf-8")])
    for noisy in ("httpx2", "google_genai", "comtypes"):  # keep the log about our steps
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log.info("Run folder: %s", run_dir)

    try:
        # Imported here so that "--help" works even without the Windows-only packages.
        from . import extract, validate

        # Stage 1: image -> Order
        order = extract.load_or_extract(args.image, json_path, use_cached=args.from_json)
        log.info("Order %s (%s items) %s %s", order.external_reference, len(order.items),
                 "loaded from" if args.from_json else "extracted by the LLM and saved in", json_path)
        # Stage 2: checks before Fakturama is touched
        validate.check(order)
        log.info("Validation passed: lines and totals add up")
        scores = extract.check_witness(order, args.image)
        log.info("OCR witness confirmed all %s text values (lowest score %.2f)",
                 len(scores), min(scores.values()))
        if args.extract_only:
            return 0

        # Stages 3 and 4: drive Fakturama, then verify
        from . import flow, ui
        ui.SLOW = args.slow
        if args.slow:
            log.info("Slow motion: %s s after every action", args.slow)
        flow.run(order, run_dir)
        log.info("DONE: Order and paid Invoice saved and verified. Evidence in %s", run_dir)
        return 0
    except ManualReview as stop:
        log.error("STOPPED for manual review: %s", stop)
        try:
            from . import ui
            ui.screenshot(ui.main_window(), run_dir / "stopped.png")
        except Exception:  # Fakturama may not even be running
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
