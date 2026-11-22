# Design: Fakturama Image-to-Cash Automation

> Part 1 of the take-home. There's no code here. The Fakturama behaviour this design relies on was checked against the real app, first by inspecting it with UI Automation, then while building the program.

## 1. Goal

- **Input:** one sales-order image, for example [`samples/order.png`](../samples/order.png).
- **Output:** in Fakturama, a saved **Order** and an **Invoice** created from it as a follow-up document. Both carry the values from the image.
  - Any missing master data is created along the way: the Debtor, the payment method, the VAT rate and the Products.
  - The Invoice is marked paid if the image says PAID.
- **Done means:** the Data > Documents list shows the Order as *open* and the Invoice as *paid*, both with the same Cust.Ref. and the source gross total. If that can't happen, the run **stops on its own**, with a clear reason, a screenshot, and a log of what was already saved.

## 2. Overview

```mermaid
flowchart LR
    IMG[/"Order image<br>(PNG)"/] --> EX["1. Extract<br>vision LLM → JSON"]
    EX --> VAL{"2. Validate<br>math, formats, OCR witness"}
    VAL -- "passes" --> DRIVE["3. Drive Fakturama<br>UIA · table copy · OCR"]
    DRIVE --> VER{"4. Verify<br>Data > Documents"}
    VER -- "matches" --> DONE(["Order open<br>Invoice paid"])
    VAL -- "fails" --> STOP(["Stop for<br>manual review"])
    DRIVE -- "ambiguous or error" --> STOP
    VER -- "mismatch" --> STOP
```

- **One command-line Python program** runs one linear flow. It's split into small modules: the order schema, extraction, validation, OCR, UI helpers, the flow steps, and a main entry point.
- **Every step is logged and screenshotted** into `runs/<timestamp>/`.
- **Every check that fails stops the run** for manual review. The program never guesses, and never deletes or edits existing records.

## 3. Extracting data from the image

**Approach:**
- Send the image to a **vision LLM**: Google Gemini on its free tier. Require a reply that matches a fixed JSON schema (structured output), so the reply always parses.
- Save the result next to the image as `<image>.extracted.json` (for the sample, `samples/order.extracted.json`).
- A `--from-json` option lets later runs skip the model. That makes them free, fast and repeatable, and a reviewer can run the project without an API key.

**Fields:**
- external reference, order date, currency, paid status
- payment method and payment date
- debtor: company, alias, first and last name, email, phone
- billing and delivery address: name, street, ZIP, city, country
- items: SKU, description, quantity, unit, unit net price, discount %, VAT %, line net
- totals: net, VAT, gross

The image's Customer ID is read but not used, because Fakturama proposes its own.

**Normalization:**
- The prompt asks the model to copy text exactly, write dates as ISO dates (`2026-07-14`) and numbers as plain numbers (`10` for `10%`), and use `null` for anything not printed instead of guessing.
- The schema (pydantic) turns the reply into real types: dates, and money as `Decimal`, never `float`.
- Validation rounds money to cents, half up, and checks the payment method against the known names (section 4).

**Why an LLM and not OCR here:**
- OCR returns text and positions but no meaning. Turning it into "billing street" or "line 2 discount" needs layout rules written for each template.
- A vision model reads the structure directly.
- Its risks are handled as follows:
  - confident mistakes: caught by the validation in section 4
  - needing an API key: a free Gemini key is enough, since there's one call per image and the result is cached
  - sending data to a third party: on the free tier Google may use what it receives, which is acceptable here because the sample data is synthetic
- **Fallback when Gemini is unavailable** (overloaded, rate-limited, or no answer within 20 s):
  - The next free model is tried. There are three, each with its own capacity, and each read the sample exactly like the human reading. The fast "lite" model (about 3 s) comes first.
  - If none answers, the run stops for manual review within about a minute. A saved reading (`--from-json`) or a JSON written by hand from the image still works, and goes through the same checks.
- **OCR is not a fallback reader.** It returns text and positions, not meaning: turning them into fields would need layout rules for each template. (The first OCR engine, Windows' built-in one, also read `CHR-ERG-01` as `CHR-ERG-OI` and missed the `10%` discount.) OCR is used as a second, independent reader instead (section 4), and for reading the screen (section 5).

## 4. Validating before touching Fakturama

Fakturama is only touched once all of these pass:

1. **Presence and format.** Every field the schema requires is there. The reference isn't empty, there is at least one item, the email looks like an email, each ZIP is 3–10 letters, digits, spaces or dashes, quantities are positive, prices aren't negative, and discounts are between 0 and 100 %.
2. **Line math.** `qty × unit_net × (1 − discount/100) = line_net`, exact to the cent.
3. **Total math.**
   - `Σ line_net = net`
   - `Σ line_net × vat/100` (rounded) `= VAT`
   - `net + VAT = gross`
4. **Business rules.**
   - The currency is EUR.
   - The payment method has a known Fakturama code: Bank Transfer → *Credit transfer*, Credit Card → *Credit card*, SEPA Direct Debit → *SEPA direct debit*.
   - The paid status is PAID or UNPAID, and PAID requires a payment date.
5. **OCR witness.** Math can't check names or streets. So every text value from the LLM must also appear, roughly (similarity ≥ 0.7, spaces ignored), in what OCR (RapidOCR) reads from the same image. That catches invented text, not one-letter typos.
6. **Derived values** are computed from the checked data. Example: the product's gross list price is `round(unit_net × (1 + vat/100), 2)`, which gives 297.50 and 47.60 for the sample.

If any check fails, the run stops before Fakturama is touched.

## 5. Finding and using Fakturama's controls (grounding)

Inspecting the app showed that most controls are native and named, that AutomationIds are random, and that **tables are custom-drawn and invisible to UIA**. The rules, in order:

1. **UIA by ControlType + Name**, searched only inside the right window, dialog or editor tab.
2. **Label anchoring** for unnamed inputs (No., Date, ZIP/City, …): find the label, then take the next input of the expected type after it.
3. **Unnamed icons** (select address, select product) are identified by their order next to their label. They're clicked at the center of their **UIA rectangle, read at runtime**. No coordinates are ever written in code.
4. **Act like a user, then read back.**
   - Text is **typed with real keystrokes**, because Fakturama sometimes shows but doesn't save a value set through `SetValue`. Dates are typed part by part.
   - Buttons use `Invoke`, checkboxes use `Toggle`, and drop-downs are opened and their entry clicked.
   - Every value is re-read after it's set. Where read-back doesn't work (search boxes), the program checks the effect instead.
5. **Tables:**
   - First, OCR the table's rectangle (RapidOCR) to answer one question: *does it have rows?*
   - If it does, copy them (focus the table, Ctrl+A, Ctrl+C) and parse the tab-separated text.
   - Copy gives exact values. OCR comes first because copying an **empty** table crashes Fakturama.
   - To **select a row or edit a cell**, the program clicks where OCR sees that row's text or that column's header on a fresh screenshot, computed at runtime. Before each such screenshot the mouse moves to the window's title bar, so no tooltip covers the table. The Debtor's row is clicked as "the only row" after its search is narrowed to one customer number, and a column is located before its line is edited.
   - In Data > Documents, every lookup first selects the category it needs, *Orders* or *Invoices*, because Fakturama remembers a category filter.
6. **Waiting:** controls, dialogs and tabs are waited for with a timeout (10 s), and values are read back to confirm an action worked. On top of that there are short fixed pauses (0.3–2 s) after actions Fakturama needs time to process, such as filtering a list after a search.
7. **Watchdog:** at pre-flight and after every save, the program looks for error and warning dialogs such as "Internal Error" or "Duplicate Contact". Closing an editor that still asks to save also stops the run. Each stop leaves a screenshot.
8. **Preconditions:**
   - Fakturama is running: its main window is found by its title, `Fakturama - <workspace path>`. Which workspace it is isn't checked.
   - The window is restored and focused.
   - No dialog is open, and no editor tab is open under a name the program is about to use ("New Order", …).

**Why no vision LLM for the screen:** it's slower, costs money on every step, and isn't deterministic. UIA plus the app's own copy covers every screen used.

## 6. The flow, step by step

The flow is **Order-first**, as the brief asks. The Order tab stays open for the whole run. After every detour, the program switches back to the Order by its tab name and re-reads Date, Cust.Ref. and Net mode.

```mermaid
flowchart TD
    P["Payment method:<br>reuse or create"] --> A
    A["New Order<br>Date, Cust.Ref., Net, With VAT"] --> B{"Debtor in the<br>address selector?"}
    B -- "one exact match" --> D["Debtor selected in the Order"]
    B -- "none" --> C["Create Debtor"] --> D
    D --> E{"For each item:<br>Product in the selector?"}
    E -- "one exact match" --> G["Line: qty, discount"]
    E -- "none" --> F["VAT: reuse or create<br>Create Product"] --> G
    G --> H["Save Order"]
    H --> I["Follow-up → Invoice<br>payment method, paid"]
    I --> J["Verify in Data > Documents"]
    B -- "ambiguous" --> S(["Stop for manual review"])
    E -- "conflict" --> S
```

| Step | What the program does | How it confirms | Stops for manual review when |
|---|---|---|---|
| 0 Pre-flight | Checks the preconditions. Searches Data > Documents, category *Orders*, for the Cust.Ref. (Fakturama remembers the last category chosen there, so it's always set. The tree is built from the existing documents: with no *Orders* entry, there is no Order at all.) | | An Order with this Cust.Ref. already exists. This prevents a duplicate on a re-run. |
| 0b Payment method | Checked for **every** order, right after pre-flight and before any editor is open. An open editor doesn't refresh its payment list, and both a new Debtor and the Invoice need the method, even for an existing customer. Data > terms of payment: reused if it exists; otherwise new, with Name = Description = method, the mapped code, all days and discounts 0, not standard. Saves once. | The method appears in the Debtor's and the Invoice's payment lists. | The method has no known code, or exists twice. |
| 1 New Order | Create: New Order. Keeps the proposed No., sets Date and Cust.Ref., Net, With VAT. | Reads each field back. | A value doesn't stick. |
| 2 Debtor | Opens the address selector and searches the company. | Exactly one row where Company, First Name, Name, ZIP and City all match. Selects it, OK, then compares the Order's addresses with the image. | Several rows match, or a row has the company but differs elsewhere. |
| 2a Create Debtor | New contact. Keeps the Customer ID, Salutation `---`. Main address gets the Invoice role, plus a second address with the Delivery role when they differ. Miscellaneous: alias, 0 % discount, Net, payment method. Saves once. | Back in the Order, the new Debtor is now the one exact row. | Fakturama warns "Duplicate Contact", or it's still not found after saving. |
| 3 Products, each in image order | Opens the product selector and searches the SKU. Fakturama adds the product by itself when the search leaves one match. | The new line's Item No. is the SKU. | Several products have the SKU. |
| 3a VAT | Data > VATs, search `VAT <n>%`. | Reused only if Name `VAT <n>%`, the value and code S all match. Otherwise created once, without touching the standard VAT. | Same name but a different value or code. |
| 3b Create Product | Item Number = SKU, Name = Description = text, gross price (section 4), cost 0, the exact VAT, stock 0. Saves once. | Selecting it again from the Order works. | It's not found after saving. |
| 3c Line | Sets qty and line discount. | Copies the line back: qty, U.Price = unit net, VAT %, discount, line price = `qty × net × (1 − disc)`. | Any mismatch. |
| 4 Save Order | Overall discount 0 %, shipping free / 0.00. Saves once. | Totals equal the image. The tab is renamed to the new Order number. | The totals differ. |
| 5 Invoice | On the saved Order: *Create a follow-up document → Invoice*, which keeps the link. Checks the copied values. Sets the payment method. If PAID: *paid*, the payment date and the full total. Saves once. | Reads the values back. | The payment method is unavailable. |
| 6 Verify | Copies the Documents rows, once under *Orders* and once under *Invoices*. | The Invoice is *paid* with the gross total. The Order is still *open* with the same Cust.Ref. and total. | Anything differs. |

## 7. Verification and evidence

- **Fields:** every value is read back right after it's written.
- **Records:** every created Debtor or Product is re-selected from the Order. That proves it really reached the database.
- **Money:** Fakturama's totals are compared with the image's totals as `Decimal`s, not as text.
- **Final state:** the Data > Documents rows are read through copy, which gives exact raw values.
- **Evidence:** `runs/<timestamp>/` holds a log and numbered screenshots of every step, plus `stopped.png` when a run stops.

## 8. Tradeoffs

| Choice | Gained | Given up |
|---|---|---|
| Vision LLM for the image instead of OCR + rules | Reads structure, and tolerates layout changes | Needs a free key and internet; can be confidently wrong (hence section 4) |
| UIA names and label anchoring instead of coordinates or screen vision | Survives moved or resized windows; fast; free | Depends on English labels and on the tree order |
| The app's copy for tables instead of OCR everywhere | Exact values (`100.0`, full dates) | Relies on a NatTable feature, and needs the empty-table guard |
| Reading the UI instead of the HSQLDB database | Verifies what a user sees, as the brief asks | More UI work than a SQL query |
| Order-first (the brief) instead of creating all data first | The selectors double as existence checks and save proofs | More tab switching and state to carry |
| Stop on ambiguity instead of a best guess | No wrong records in accounting data | Some runs need a human |
| One linear script instead of a framework | Easy to read and explain | Less reuse if more document types are added |

## 9. Out of scope and limitations

- **Tested scope:** Fakturama 2.2.0, English UI, en-US locale, one image per run, one currency, and the three mapped payment methods.
- **Fragile points:**
  - Unnamed icons are found by their order, and table reading depends on NatTable's copy support. A Fakturama update could break either.
  - Pre-flight checks and the watchdog make that **fail loudly, not silently**.
- **During a run:** the desktop must stay unlocked and Fakturama visible, because icon and tab clicks and OCR need real pixels.
- **Partial runs:** if a run stops midway, the master data it already created remains. Those records are valid, and the next run reuses them.
- **Not done:** delivery notes, dunning, corrections, and the web-shop import.
