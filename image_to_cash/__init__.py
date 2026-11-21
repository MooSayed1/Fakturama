"""Fakturama Image-to-Cash: one order image in, a verified Order and paid Invoice out.

    __main__.py   the command line: read -> check -> drive Fakturama -> verify
    errors.py     ManualReview: the one way the program stops
    schema.py     the Order data: the JSON schema the LLM must follow, and the parsed result
    extract.py    image -> Order: the Gemini call, the JSON cache, the OCR witness
    validate.py   format, line and total checks before Fakturama is touched
    ocr.py        Windows' built-in OCR engine
    ui.py         UI Automation helpers: find, type, choose, tables, watchdog
    flow.py       the Fakturama steps: Order -> Debtor -> Products -> Invoice -> verify
"""
