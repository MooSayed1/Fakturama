"""The Fakturama part of the run, Order-first (docs/DESIGN.md section 6).

Read run() top to bottom: it is the whole story. Each step below does
something, checks it, and raises ManualReview when a check fails. The Order
tab stays open the whole time; Debtors, payment methods, VATs and Products
are found or created in other tabs, then the flow comes back to it.
"""
import logging
import time
from decimal import Decimal
from pathlib import Path

from . import ui
from .errors import ManualReview
from .schema import Address, Item, Order
from .validate import PAYMENT_CODES, expected_line_net, gross_list_price, money

log = logging.getLogger("image_to_cash")


# ---------- the run's context ----------

class Run:
    """Passed to every step, so each step can reach the window and save screenshots."""

    def __init__(self, run_dir: Path):
        self.dir = run_dir
        (run_dir / "ocr").mkdir(parents=True, exist_ok=True)
        self.window = ui.main_window()
        self.count = 0

    def shot(self, name: str) -> None:
        """Numbered screenshot of the whole window, e.g. 03-order-header.png."""
        self.count += 1
        ui.screenshot(self.window, self.dir / f"{self.count:02d}-{name}.png")

    def scratch(self, name: str) -> Path:
        """Where the screenshots used for OCR go (runs/<time>/ocr/)."""
        return self.dir / "ocr" / f"{name}.png"


# ---------- the whole flow, and step 0: pre-flight ----------

# Editor tabs this program opens. If one is already open, names would clash.
OUR_TABS = {"New Order", "New Debtor", "New product", "New TAX Rate",
            "New Term of Payment", "New Invoice"}


def run(order: Order, run_dir: Path) -> None:
    r = Run(run_dir)
    preflight(r, order)                                       # 0. safe to start?
    ensure_payment_method(r, order.payment.method)            #    payment method exists (G23)
    open_order(r, order)                                      # 1. New Order header
    resolve_debtor(r, order)                                  # 2. Debtor
    for number, item in enumerate(order.items, start=1):
        resolve_product_line(r, order, item, number)          # 3. Products (+ VAT) and lines
    order_no = save_order(r, order)                           # 4. totals, save the Order
    invoice_no = create_invoice(r, order, order_no)           # 5. follow-up Invoice, paid
    verify_documents(r, order, order_no, invoice_no)          # 6. Data > Documents


def preflight(r: Run, order: Order) -> None:
    """Step 0: no error dialog, no open dialog, no clashing editor tab, no duplicate Order."""
    ui.check_no_error_dialog()
    dialogs = [c.Name for c in r.window.GetChildren() if c.ControlTypeName == "WindowControl"]
    if dialogs:
        raise ManualReview(f"close the open Fakturama dialog(s) first: {dialogs}")
    clashing = [t for t in ui.tab_names(r.window) if t.lstrip("*") in OUR_TABS]
    if clashing:
        raise ManualReview(f"close these editor tabs first: {clashing}")
    duplicate = existing_order(r, order.external_reference)
    if duplicate:  # re-running must not create a second Order
        raise ManualReview(f"Order {duplicate} already has Cust.Ref. {order.external_reference}")
    log.info("Pre-flight OK: no dialog, no clashing tab, no Order with Cust.Ref. %s yet",
             order.external_reference)
    r.shot("preflight")


# ---------- steps 0 + 6: Data > Documents ----------

def document_rows(r: Run, search: str) -> list[list[str]]:
    """Rows of Data > Documents filtered by `search`, copied with exact values.
    Columns: icon, number, date, name, Cust.Ref., state, total, printed."""
    ui.open_menu(r.window, "Data", "Documents")
    view = ui.find(r.window, "TabControl", "Documents", depth=10)
    table = ui.table_in(view)
    ui.set_search(ui.after_label(view, "Search:"), search)
    if not ui.table_has_rows(table, r.scratch("documents-search")):
        return []
    return ui.copy_table(table)


def existing_order(r: Run, external_reference: str) -> str | None:
    """The number of an Order that already has this Cust.Ref., if any."""
    for row in document_rows(r, external_reference):
        if row[0] == "ICON_ORDER" and row[4] == external_reference:
            return row[1]
    return None


def verify_documents(r: Run, order: Order, order_no: str, invoice_no: str) -> None:
    """Step 6: the Order must be 'open', the Invoice 'paid' (if PAID), both
    with the image's Cust.Ref. and gross total."""
    rows = {row[1]: row for row in document_rows(r, order.external_reference)}
    r.shot("documents")
    expected = {order_no: ("ICON_ORDER", "COMMAND_ORDER_PENDING"),
                invoice_no: ("ICON_INVOICE", "COMMAND_CHECKED" if order.payment.status == "PAID"
                             else None)}  # unpaid state: not checked
    for number, (icon, state) in expected.items():
        row = rows.get(number)
        if row is None:
            raise ManualReview(f"{number} is not in Data > Documents for {order.external_reference}")
        problems = []
        if row[0] != icon:
            problems.append(f"type {row[0]}")
        if row[4] != order.external_reference:
            problems.append(f"Cust.Ref. {row[4]}")
        if state is not None and row[5] != state:
            problems.append(f"state {row[5]} (expected {state})")
        if money(Decimal(row[6])) != money(order.totals.gross):
            problems.append(f"total {row[6]}")
        if problems:
            raise ManualReview(f"Data > Documents row for {number} is wrong: {', '.join(problems)}")
        log.info("Documents: %s %s, %s, Cust.Ref. %s, total %s", number, row[2], row[5], row[4], row[6])


# ---------- steps 1 + 4: the Order editor ----------

ORDER_TAB = "New Order"  # the tab's name until the Order is saved


def open_order(r: Run, order: Order) -> None:
    """Step 1: New Order with the proposed No., the order date, Cust.Ref., Net, With VAT."""
    ui.press(ui.find(r.window, "ButtonControl", "Create: New Order", depth=8))
    ed = ui.editor(r.window, ORDER_TAB)
    log.info("New Order opened, proposed No. %s (kept)", ui.get_text(ui.after_label(ed, "No.")))
    ui.type_date(ui.after_label(ed, "Date"), order.order_date, "order date")
    ui.set_text(ui.find(ed, "EditControl", "Cust.Ref."), order.external_reference)
    ui.choose(ui.after_label(ed, "Date", "ComboBoxControl"), "Net")  # the unnamed Gross/Net box
    vat_mode = ui.find(ed, "ComboBoxControl", "VAT")
    if ui.get_text(vat_mode).strip() != "With VAT":
        ui.choose(vat_mode, "With VAT")
    r.shot("order-header")


def back_to_order(r: Run, order: Order):
    """After every detour: switch back to the Order and make sure our values
    survived. Returns the Order's editor pane."""
    ui.switch_to_tab(r.window, ORDER_TAB)
    ed = ui.editor(r.window, ORDER_TAB)
    shown_date = ui.parse_ui_date(ui.get_text(ui.after_label(ed, "Date")))
    ref = ui.get_text(ui.find(ed, "EditControl", "Cust.Ref."))
    mode = ui.get_text(ui.after_label(ed, "Date", "ComboBoxControl")).strip()
    if (shown_date, ref, mode) != (order.order_date, order.external_reference, "Net"):
        raise ManualReview(f"the Order header changed: date={shown_date}, Cust.Ref.={ref!r}, mode={mode!r}")
    return ed


def check_totals(ed, order: Order, what: str) -> None:
    """The editor's Total Net / VAT / Total must equal the image's totals."""
    shown = {
        "net": ui.number_in(ui.get_text(ui.find(ed, "EditControl", "Total Net"))),
        "vat": ui.number_in(ui.get_text(ui.find(ed, "EditControl", "VAT"))),
        "gross": ui.number_in(ui.get_text(ui.find(ed, "EditControl", "Total"))),
    }
    expected = {"net": money(order.totals.net), "vat": money(order.totals.vat),
                "gross": money(order.totals.gross)}
    if {k: money(v) for k, v in shown.items()} != expected:
        raise ManualReview(f"{what} totals {shown} differ from the image {expected}")
    log.info("%s totals match the image: %s", what, expected)


def save_order(r: Run, order: Order) -> str:
    """Step 4: overall discount 0 %, free shipping, totals = image, save once.
    Returns the Order's number (its tab is renamed to it)."""
    ed = back_to_order(r, order)
    if ui.number_in(ui.get_text(ui.find(ed, "EditControl", "Discount"))) != 0:
        raise ManualReview("the Order's overall discount is not 0%")
    shipping = ui.find(ed, "ComboBoxControl", "Shipping")
    if ui.get_text(shipping).strip() != "Free of shipping costs":
        ui.choose(shipping, "Free of shipping costs")
    check_totals(ed, order, "Order")
    number = ui.get_text(ui.after_label(ed, "No."))
    r.shot("order-before-save")
    ui.save_active_editor(r.window, ed)  # save once
    if number not in [t.lstrip("*") for t in ui.tab_names(r.window)]:
        raise ManualReview(f"after saving, no tab named {number}")
    log.info("Order %s saved", number)
    r.shot("order-saved")
    return number


# ---------- step 2: the Debtor (+ payment method) ----------

def resolve_debtor(r: Run, order: Order) -> None:
    """Select the Debtor in the Order; if it doesn't exist, create it, then select it."""
    if select_debtor(r, order):
        log.info("Debtor %s found and selected", order.debtor.company)
    else:
        log.info("Debtor %s not found: creating it", order.debtor.company)
        create_debtor(r, order)
        back_to_order(r, order)
        if not select_debtor(r, order):  # also proves the new Debtor was saved
            raise ManualReview("the new Debtor can't be selected from the Order: was it saved?")
    check_order_addresses(r, order)
    r.shot("order-debtor")


# ---------- selecting ----------

def select_debtor(r: Run, order: Order) -> bool:
    """Search the company in the Order's address selector. True if selected.
    One exact row: select it. None: False. Ambiguous: stop."""
    d = order.debtor
    ed = ui.editor(r.window, ORDER_TAB)
    ui.click(ui.icons_next_to(ed, "Addresses")[0])  # upper icon = existing contact
    dialog = ui.dialog(r.window, "Select the address")
    table = ui.table_in(dialog)
    search = ui.after_label(dialog, "Search:")
    ui.set_search(search, d.company)
    if not ui.table_has_rows(table, r.scratch("address-search")):
        ui.press(ui.find(dialog, "ButtonControl", "Cancel"))
        return False
    # Columns: No., First Name, Name, Company, ZIP, City, ...  ('null' = empty)
    rows = [[("" if v == "null" else v) for v in row] for row in ui.copy_table(table)]
    same_company = [row for row in rows if row[3] == d.company]
    exact = [row for row in same_company
             if (row[1], row[2], row[4], row[5]) == (d.first_name, d.last_name,
                                                     order.billing_address.zip, order.billing_address.city)]
    if len(exact) > 1 or (same_company and not exact):
        ui.press(ui.find(dialog, "ButtonControl", "Cancel"))
        raise ManualReview(f"Debtor search for {d.company!r} is ambiguous: {same_company}")
    if not exact:
        ui.press(ui.find(dialog, "ButtonControl", "Cancel"))
        return False
    # Narrow the list to that one customer number, click its row, OK (UI-MAP G20).
    ui.set_search(search, exact[0][0])
    ui.click_only_row(table, r.scratch("address-click"))
    ui.press(ui.find(dialog, "ButtonControl", "OK"))
    time.sleep(1)
    return True


def address_lines(r: Run, role: str) -> list[str]:
    """The address block the Order shows on its 'Invoice address' tab."""
    ed = ui.editor(r.window, ORDER_TAB)
    tab = ui.find(ed, "TabItemControl", role)
    ui.click(tab)
    box = tab.GetParentControl()
    text = ui.get_text([c for c in box.GetChildren() if c.ControlTypeName == "EditControl"][0])
    return [line.strip() for line in text.splitlines() if line.strip()]


def missing_parts(lines: list[str], address: Address) -> list[str]:
    """What is missing from an address block. Fakturama prints the ZIP line
    with a country prefix, e.g. 'DE-10117 Berlin' (UI-MAP G27)."""
    missing = []
    if address.street not in lines:
        missing.append(address.street)
    if not any(line.endswith(f"{address.zip} {address.city}") for line in lines):
        missing.append(f"{address.zip} {address.city}")
    return missing


def check_order_addresses(r: Run, order: Order) -> None:
    billing = address_lines(r, "Invoice address")
    missing = missing_parts(billing, order.billing_address)
    if order.debtor.company not in billing:
        missing.append(order.debtor.company)
    if missing:
        raise ManualReview(f"the Order's invoice address {billing} lacks {missing}")
    log.info("Order invoice address OK: %s", billing)
    if "Delivery address" in ui.tab_names(r.window):
        delivery = address_lines(r, "Delivery address")
        missing = missing_parts(delivery, order.delivery_address)
        if missing:
            raise ManualReview(f"the Order's delivery address {delivery} lacks {missing}")
        log.info("Order delivery address OK: %s", delivery)


# ---------- creating ----------

def create_debtor(r: Run, order: Order) -> None:
    """New contact, filled from the image, saved once. Its payment method
    already exists: run() makes sure of that before any editor is open."""
    d, bill, ship = order.debtor, order.billing_address, order.delivery_address
    ui.press(ui.find(r.window, "SplitButtonControl", "Create a new contact", depth=8))
    ed = ui.editor(r.window, "New Debtor")
    log.info("New Debtor: Customer ID %s (kept)", ui.get_text(ui.find(ed, "EditControl", "Customer ID")))
    ui.set_text(ui.find(ed, "EditControl", "Company"), d.company)
    ui.set_text(ui.after_label(ed, "First Name Last Name", index=0), d.first_name)
    ui.set_text(ui.after_label(ed, "First Name Last Name", index=1), d.last_name)

    # Main address = billing address (+ the Delivery role when both are the same)
    same = (ship.street, ship.zip, ship.city) == (bill.street, bill.zip, bill.city)
    fill_address(ed, bill, roles=["Invoice address"] + (["Delivery address"] if same else []))
    ui.set_text(ui.find(ed, "EditControl", "E-Mail"), d.email or "")
    ui.set_text(ui.find(ed, "EditControl", "Telephone"), d.phone or "")
    if not same:  # a second address with the Delivery role
        ui.press(ui.find(ed, "ButtonControl", "+"))
        ui.click(ui.find(ed, "TabItemControl", "additional address #1"))
        ui.set_text(ui.find(ed, "EditControl", "additional name"), ship.name)
        fill_address(ed, ship, roles=["Delivery address"])
        ui.click(ui.find(ed, "TabItemControl", "Main address"))

    # Miscellaneous tab: alias, discount 0 %, Net, payment method
    ui.click(ui.find(ed, "TabItemControl", "Miscellaneous"))
    ui.set_text(ui.find(ed, "EditControl", "Alias name"), d.alias or "")
    discount = ui.find(ed, "EditControl", "Discount")
    if ui.number_in(ui.get_text(discount)) != 0:
        ui.type_number(discount, Decimal(0), "debtor discount")
    ui.choose(ui.find(ed, "ComboBoxControl", "Net or Gross"), "Net")
    payment = ui.find(ed, "ComboBoxControl", "Payment")
    if order.payment.method not in ui.options(payment):
        raise ManualReview(f"payment method {order.payment.method!r} is not offered to the Debtor")
    ui.choose(payment, order.payment.method)

    r.shot("debtor-filled")
    ui.save_active_editor(r.window, ed)  # save once
    log.info("Debtor saved")
    r.shot("debtor-saved")
    ui.close_active_tab(r.window)


def fill_address(ed, address: Address, roles: list[str]) -> None:
    """Fill the address fields that are visible right now, and the address role(s)."""
    ui.set_text(ui.find(ed, "EditControl", "Street"), address.street)
    ui.set_text(ui.after_label(ed, "ZIP - City", index=0), address.zip)
    ui.set_text(ui.after_label(ed, "ZIP - City", index=1), address.city)
    ui.choose(ui.find(ed, "ComboBoxControl", "Country"), address.country)
    # The role picker is a small popup with one checkbox per role.
    window = ed.GetTopLevelControl()
    ui.click(ui.after_label(ed, "address type", "ButtonControl"))
    time.sleep(1)
    popup = ui.find_where(window, lambda c: c.ClassName == "#32770" and c.ControlTypeName == "PaneControl",
                           "address type popup", depth=1)
    for role in ("Invoice address", "Delivery address"):
        ui.set_checked(ui.find(popup, "CheckBoxControl", role), role in roles)
    ui.find(ed, "EditControl", "Street").SetFocus()  # closes the popup
    time.sleep(0.5)
    shown = ui.get_text(ui.after_label(ed, "address type"))
    if any(role not in shown for role in roles):
        raise ManualReview(f"address type shows {shown!r}, expected {roles}")


def ensure_payment_method(r: Run, method: str) -> None:
    """Data > terms of payment: reuse the method if it exists, else create it once.

    Runs at the start of every order, before any editor is open: an open
    editor doesn't refresh its payment list (UI-MAP G23), and both a new
    Debtor and the Invoice need the method, even for an existing customer."""
    ui.open_menu(r.window, "Data", "terms of payment")
    view = ui.find(r.window, "TabControl", "terms of payment", depth=10)
    table = ui.table_in(view)
    ui.set_search(ui.after_label(view, "Search:"), method)
    if ui.table_has_rows(table, r.scratch("payment-search")):
        rows = ui.copy_table(table)  # columns: ..., Name, Description, ...
        log.info("Payment search %r rows: %s", method, rows)
        matching = [row for row in rows if method in row]
        if len(matching) == 1:
            log.info("Payment method %r exists: reused", method)
            return
        if matching:
            raise ManualReview(f"payment method {method!r} exists more than once: {matching}")
    log.info("Payment method %r missing: creating it", method)
    ui.press(ui.find(view, "ButtonControl", "Create a new term of payment"))
    ed = ui.editor(r.window, "New Term of Payment")
    ui.set_text(ui.find(ed, "EditControl", "Name"), method)
    ui.set_text(ui.find(ed, "EditControl", "Description"), method)
    ui.choose(ui.find(ed, "ComboBoxControl", "!editorPaymentPaymentcode!"), PAYMENT_CODES[method])
    for field in ("Cash discount", "Discount Days", "Net Days"):
        if ui.number_in(ui.get_text(ui.find(ed, "EditControl", field))) != 0:
            raise ManualReview(f"new payment method: {field} is not 0")
    r.shot("payment-method-filled")
    ui.save_active_editor(r.window, ed)  # save once; "Set as standard" is NOT pressed
    log.info("Payment method %r saved (code %s)", method, PAYMENT_CODES[method])
    ui.close_active_tab(r.window)


# ---------- step 3: Products (+ VAT) and lines ----------

def resolve_product_line(r: Run, order: Order, item: Item, number: int) -> None:
    """Add the item's Product to the Order (creating it if needed), then set the line."""
    if select_product(r, item):
        log.info("Product %s found and added", item.sku)
    else:
        log.info("Product %s not found: creating it", item.sku)
        create_product(r, item)
        back_to_order(r, order)
        if not select_product(r, item):  # also proves the new Product was saved
            raise ManualReview(f"the new Product {item.sku} can't be selected: was it saved?")
    set_line(r, item)
    r.shot(f"order-line-{number}")


# ---------- selecting and the line ----------

def item_rows(r: Run) -> list[list[str]]:
    """The Order's item lines, copied. Columns: Qty, Item No., Picture, Name,
    Description, VAT, U.Price, Discount, Price."""
    table = ui.table_in(ui.editor(r.window, ORDER_TAB))
    if not ui.table_has_rows(table, r.scratch("items")):
        return []
    return ui.copy_table(table)


def select_product(r: Run, item: Item) -> bool:
    """Search the SKU in the Order's product selector. True if its line was added.

    Fakturama adds the product by itself and closes the dialog when the search
    leaves exactly one match (UI-MAP G21), so the added line is always checked.
    """
    ed = ui.editor(r.window, ORDER_TAB)
    lines_before = len(item_rows(r))
    ui.click(ui.icons_next_to(ed, "Items")[0])  # upper icon = existing product
    dialog = ui.dialog(r.window, "Select a product")
    table = ui.table_in(dialog)
    ui.set_search(ui.after_label(dialog, "Search:"), item.sku)
    if dialog.Exists(0.5):  # still open: zero or several matches
        if not ui.table_has_rows(table, r.scratch("product-search")):
            ui.press(ui.find(dialog, "ButtonControl", "Cancel"))
            return False
        rows = ui.copy_table(table)  # columns: Item No., Name, Description, ...
        exact = [row for row in rows if row[0] == item.sku]
        if len(exact) != 1:
            ui.press(ui.find(dialog, "ButtonControl", "Cancel"))
            if exact:
                raise ManualReview(f"{len(exact)} products have Item No. {item.sku}")
            return False
        ui.click_text(table, item.sku, r.scratch("product-click"))
        ui.press(ui.find(dialog, "ButtonControl", "OK"))
        time.sleep(1)
    rows = item_rows(r)
    if len(rows) != lines_before + 1 or rows[-1][1] != item.sku:
        raise ManualReview(f"expected a new line for {item.sku}, the item list shows {rows}")
    return True


def set_line(r: Run, item: Item) -> None:
    """Type qty and discount into the new (last) line, then check its numbers."""
    table = ui.table_in(ui.editor(r.window, ORDER_TAB))
    # Find the Discount column now, before any cell is edited (UI-MAP G30).
    discount_column = (ui.column_position(table, "Discount", r.scratch("discount-column"))
                       if item.discount_pct != 0 else None)
    if item.qty != 1:
        ui.click_first_decimal_in_last_row(table, r.scratch("qty-cell"))
        ui.keys(f"{item.qty:f}")
        ui.keys("{Enter}")
        time.sleep(0.8)
    if item.discount_pct != 0:
        ui.click_cell(table, discount_column, r.scratch("discount-cell"))
        ui.keys(f"{item.discount_pct:f}")
        ui.keys("{Enter}")
        time.sleep(0.8)
    qty, sku, _pic, _name, _desc, vat, unit, discount, price = item_rows(r)[-1][:9]
    problems = []
    if Decimal(qty) != item.qty:
        problems.append(f"qty {qty} != {item.qty}")
    if money(ui.number_in(unit)) != money(item.unit_net):
        problems.append(f"U.Price {unit} != {item.unit_net}")
    if f"taxValue: [{item.vat_pct / 100:g}]" not in vat:
        problems.append(f"VAT {vat[:40]}... is not {item.vat_pct}%")
    if Decimal(discount) * -100 != item.discount_pct:  # Fakturama stores 10 % as -0.1
        problems.append(f"discount {discount} != {item.discount_pct}%")
    if money(ui.number_in(price)) != expected_line_net(item):
        problems.append(f"line price {price} != {expected_line_net(item)}")
    if problems:
        raise ManualReview(f"item {sku}: " + "; ".join(problems))
    log.info("Line %s OK: %s x %s, -%s%% = %s", sku, qty, unit, item.discount_pct, price)


# ---------- creating ----------

def ensure_vat(r: Run, rate: Decimal) -> str:
    """Data > VATs: reuse 'VAT <n>%' only if its value and code S match; else create it once."""
    name = f"VAT {rate:g}%"
    ui.open_menu(r.window, "Data", "VATs")
    view = ui.find(r.window, "TabControl", "VATs", depth=10)
    table = ui.table_in(view)
    ui.set_search(ui.after_label(view, "Search:"), name)
    if ui.table_has_rows(table, r.scratch("vat-search")):
        rows = ui.copy_table(table)  # columns: standard?, Name, Description, value (0.19)
        log.info("VAT search %r rows: %s", name, rows)
        same_name = [row for row in rows if row[1] == name]
        if len(same_name) > 1:
            raise ManualReview(f"VAT {name} exists more than once: {same_name}")
        if same_name:
            if Decimal(same_name[0][3]) * 100 != rate:
                raise ManualReview(f"VAT {name} exists with value {same_name[0][3]}, not {rate}%")
            # The list has no code column: open the VAT and read its code.
            ui.click_text(table, "VAT", r.scratch("vat-open"), double=True)
            ed = ui.editor(r.window, name)
            code = ui.get_text(ui.find(ed, "ComboBoxControl", "VAT code (E-Invoice)")).strip()
            ui.close_active_tab(r.window)
            if not code.startswith("S "):
                raise ManualReview(f"VAT {name} exists with code {code!r}, not S (Standard rate)")
            log.info("VAT %s exists with value %s%% and code %s: reused", name, rate, code)
            return name
    ui.press(ui.find(view, "ButtonControl", "Create a new tax rate"))
    ed = ui.editor(r.window, "New TAX Rate")
    ui.set_text(ui.find(ed, "EditControl", "Name"), name)
    ui.set_text(ui.find(ed, "EditControl", "Description"), name)
    ui.choose(ui.find(ed, "ComboBoxControl", "VAT code (E-Invoice)"), "S (Standard rate)")
    ui.type_number(ui.find(ed, "EditControl", "Value"), rate, "VAT value")
    r.shot("vat-filled")
    ui.save_active_editor(r.window, ed)  # save once; the standard VAT is not touched
    log.info("VAT %s created", name)
    ui.close_active_tab(r.window)
    return name


def create_product(r: Run, item: Item) -> None:
    """New product: SKU, names, gross list price, cost 0, the exact VAT, stock 0. Saved once."""
    vat_name = ensure_vat(r, item.vat_pct)  # the VAT is checked first, as the brief asks
    ui.press(ui.find(r.window, "ButtonControl", "Create a new product", depth=8))
    ed = ui.editor(r.window, "New product")
    ui.set_text(ui.find(ed, "EditControl", "Item Number"), item.sku)
    ui.set_text(ui.find(ed, "EditControl", "Name"), item.description)
    ui.set_text(ui.find(ed, "EditControl", "Description"), item.description)
    ui.type_number(ui.after_label(ed, "Price (gross)"), gross_list_price(item), "gross price")
    ui.type_number(ui.after_label(ed, "cost price (net)"), Decimal(0), "cost price")
    ui.choose(ui.find(ed, "ComboBoxControl", "VAT"), vat_name)
    ui.type_number(ui.find(ed, "EditControl", "Stock"), Decimal(0), "stock")
    r.shot(f"product-{item.sku}-filled")
    ui.save_active_editor(r.window, ed)  # save once
    log.info("Product %s created at gross %s", item.sku, gross_list_price(item))
    ui.close_active_tab(r.window)


# ---------- step 5: the follow-up Invoice ----------

def create_invoice(r: Run, order: Order, order_no: str) -> str:
    """'Create a follow-up document > Invoice' on the saved Order (keeps the link),
    check what was copied, set payment + paid, save once. Returns the Invoice No."""
    ui.switch_to_tab(r.window, order_no)
    ed = ui.editor(r.window, order_no)
    group = ui.find(ed, "GroupControl", "Create a follow-up document")
    tabs_before = set(ui.tab_names(r.window))
    ui.press(ui.find(group, "ButtonControl", "Invoice"))
    time.sleep(2)
    new_tabs = [t for t in ui.tab_names(r.window) if t not in tabs_before]
    if len(new_tabs) != 1:
        raise ManualReview(f"expected one new Invoice tab, got {new_tabs}")
    tab = new_tabs[0].lstrip("*")
    inv = ui.editor(r.window, tab)
    number = ui.get_text(ui.after_label(inv, "No."))
    log.info("Invoice %s created from Order %s", number, order_no)

    # Copied from the Order? (the proposed Invoice No. and dates are kept)
    if ui.get_text(ui.find(inv, "EditControl", "Cust.Ref.")) != order.external_reference:
        raise ManualReview("the Invoice did not copy the Cust.Ref.")
    if ui.parse_ui_date(ui.get_text(ui.after_label(inv, "Order Date"))) != order.order_date:
        raise ManualReview("the Invoice's Order Date is not the order date")
    if ui.get_text(ui.after_label(inv, "Date", "ComboBoxControl")).strip() != "Net":
        raise ManualReview("the Invoice is not in Net mode")
    check_totals(inv, order, "Invoice")

    payment = payment_dropdown(inv)
    if order.payment.method not in ui.options(payment):
        raise ManualReview(f"payment method {order.payment.method!r} is not available on the Invoice")
    ui.choose(payment, order.payment.method)
    if order.payment.status == "PAID":  # otherwise it stays unpaid: nothing is invented
        mark_paid(inv, order)
    r.shot("invoice-filled")
    ui.save_active_editor(r.window, inv)  # save once
    log.info("Invoice %s saved", number)
    r.shot("invoice-saved")
    return number


def payment_dropdown(inv):
    """The Invoice's payment drop-down has no name: it is the first drop-down
    after the 'paid' checkbox in the UIA tree."""
    seen_paid = False
    for control, _depth in ui.auto.WalkControl(inv, maxDepth=30):
        if control.ControlTypeName == "CheckBoxControl" and control.Name == "paid":
            seen_paid = True
        elif seen_paid and control.ControlTypeName == "ComboBoxControl":
            return control
    raise ManualReview("could not find the Invoice's payment method drop-down")


def mark_paid(inv, order: Order) -> None:
    """Tick 'paid'; Fakturama then shows label 'at' + date and 'Value' + amount."""
    ui.set_checked(ui.find(inv, "CheckBoxControl", "paid"), True)
    time.sleep(1)
    ui.type_date(ui.after_label(inv, "at"), order.payment.date, "payment date")
    ui.type_number(ui.find(inv, "EditControl", "Value"), money(order.totals.gross), "paid value")
