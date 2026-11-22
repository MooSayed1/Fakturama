"""Small helpers for driving Fakturama through Microsoft UI Automation (UIA).

Rules (docs/DESIGN.md section 5):
- Controls are found by ControlType + Name, or by the label in front of them.
  Never by coordinates, and never by AutomationId (random in Fakturama).
- Values are typed with real keystrokes and read back; buttons are Invoked,
  checkboxes Toggled, drop-downs opened and their entry clicked.
- Clicks land on a control's UIA rectangle or, inside custom-drawn tables,
  where OCR sees the text. Both positions are computed at that moment.
- Anything missing or unexpected raises ManualReview instead of guessing.
"""
import ctypes
import re
import time
import warnings
from datetime import date, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

warnings.filterwarnings("ignore", module="comtypes")
import uiautomation as auto  # noqa: E402

from .errors import ManualReview  # noqa: E402
from .ocr import OcrWord, read_lines  # noqa: E402

TIMEOUT = 10  # seconds to wait for a control or dialog before giving up
P = auto.PatternId

# Slow motion (--slow): seconds to wait after every visible action, so a person
# can follow the run. Typing slows down too (SLOW / 10 seconds per key). 0 = full speed.
SLOW = 0.0


def pause() -> None:
    """In slow motion, wait a moment after a visible action."""
    if SLOW:
        time.sleep(SLOW)


# ---------- finding things ----------

def main_window() -> auto.WindowControl:
    """Fakturama's main window, restored and in front."""
    window = auto.WindowControl(searchDepth=1, SubName="Fakturama -")
    if not window.Exists(TIMEOUT):
        raise ManualReview("Fakturama is not running")
    if ctypes.windll.user32.IsIconic(window.NativeWindowHandle):   # minimized?
        ctypes.windll.user32.ShowWindow(window.NativeWindowHandle, 9)  # SW_RESTORE
    window.SetActive()
    return window


def find(root, control_type: str, name: str, timeout: float = TIMEOUT, depth: int = 30):
    """The first control of this type and name under `root`, waiting up to `timeout`."""
    control = root.Control(searchDepth=depth, Name=name,
                           Compare=lambda c, _depth: c.ControlTypeName == control_type)
    if not control.Exists(timeout):
        raise ManualReview(f"could not find {control_type} {name!r}")
    return control


def find_where(root, condition, what: str, timeout: float = TIMEOUT, depth: int = 30):
    """The first control under `root` for which condition(control) is true."""
    control = root.Control(searchDepth=depth, Compare=lambda c, _depth: condition(c))
    if not control.Exists(timeout):
        raise ManualReview(f"could not find {what}")
    return control


def after_label(root, label: str, control_type: str = "EditControl", index: int = 0):
    """Label anchoring: the index-th control of `control_type` after the label `label`.

    Used for inputs without a Name. Their label is a Text control just before
    them in the UIA tree, e.g. label 'ZIP - City' -> Edit (ZIP), Edit (City).
    """
    seen_label = False
    found = 0
    for control, _depth in auto.WalkControl(root, maxDepth=30):
        if not seen_label:
            seen_label = control.ControlTypeName == "TextControl" and control.Name == label
        elif control.ControlTypeName == control_type:
            if found == index:
                return control
            found += 1
    raise ManualReview(f"no {control_type} #{index} after label {label!r}")


def icons_next_to(root, label: str) -> list:
    """The unnamed icons that sit next to a label (e.g. 'Addresses', 'Items')."""
    text = find(root, "TextControl", label)
    return [c for c in text.GetParentControl().GetChildren() if c.ControlTypeName == "ImageControl"]


def dialog(window, name: str, timeout: float = TIMEOUT):
    """Wait for a dialog of Fakturama (a child window) with this title."""
    found = window.WindowControl(searchDepth=1, Name=name)
    if not found.Exists(timeout):
        raise ManualReview(f"dialog {name!r} did not appear")
    return found


def editor(window, tab_name: str):
    """The content pane of an editor tab, e.g. 'PO000002' or 'New Debtor'."""
    return find(window, "PaneControl", tab_name, depth=12)


# ---------- acting on things ----------

def keys(text: str) -> None:
    """Send keystrokes to the focused control, e.g. '{Ctrl}s'."""
    auto.SendKeys(text, waitTime=0.3)
    pause()


def click(control) -> None:
    """Click the middle of the control. The position comes from UIA right now."""
    control.Click(simulateMove=False, waitTime=0.3)
    pause()


def press(button) -> None:
    """Press a button through its InvokePattern (no mouse)."""
    pattern = button.GetPattern(P.InvokePattern)
    if pattern is None:
        click(button)
    else:
        pattern.Invoke()
        pause()
    time.sleep(0.3)


def get_text(control) -> str:
    return control.GetPattern(P.ValuePattern).Value


def set_text(edit, value: str) -> None:
    """Type a value with real keystrokes, then check that the field shows it.

    Why keystrokes: Fakturama ignores some values set through UIA's
    ValuePattern (the Company field showed the text but saved nothing,
    DESIGN.md G17). Typing is what a user does, so Fakturama always stores it.
    """
    edit.SetFocus()
    time.sleep(0.1)
    keys("{Ctrl}a")
    if value:
        auto.SendKeys(value.replace("{", "{{}").replace("}", "{}}"), interval=0.01 + SLOW / 10, waitTime=0.2)
        pause()
    else:
        keys("{Delete}")
    actual = get_text(edit)
    if actual != value:
        raise ManualReview(f"typed {value!r} into {edit.Name or 'a field'} but it reads {actual!r}")


def set_search(edit, value: str) -> None:
    """Fill a search box through ValuePattern (fast). Its read-back is always
    empty in Fakturama (DESIGN.md G15), so the caller checks the filtered rows."""
    edit.GetPattern(P.ValuePattern).SetValue(value)
    time.sleep(1.5)
    pause()


def number_in(text: str) -> Decimal:
    """'$1,297.50' -> 1297.50, '10%' -> 10, '2.00' -> 2."""
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace(",", ""))
    if not cleaned:
        raise ManualReview(f"expected a number but the field shows {text!r}")
    return Decimal(cleaned)


def type_number(edit, value: Decimal, what: str) -> None:
    """Type a number into a formatted field (money, percent), check it as a number."""
    edit.SetFocus()
    time.sleep(0.2)
    keys("{Ctrl}a")
    auto.SendKeys(f"{value:f}", interval=0.02 + SLOW / 10, waitTime=0.2)
    keys("{Tab}")  # leave the field so Fakturama parses and formats it
    shown = get_text(edit)
    if number_in(shown) != Decimal(value):
        raise ManualReview(f"{what}: typed {value} but the field shows {shown!r}")


def parse_ui_date(text: str) -> date | None:
    """'Jul 14, 2026' -> date(2026, 7, 14). Fakturama runs with the en-US locale."""
    try:
        return datetime.strptime(text.strip(), "%b %d, %Y").date()
    except ValueError:
        return None


def type_date(edit, value: date, what: str) -> None:
    """Fakturama's date fields are edited one part at a time: Home jumps to
    the month; after two digits the field moves on to the day, then the year.
    So we type MM, DD, YYYY with no arrow keys (DESIGN.md G18). Then we check."""
    edit.SetFocus()
    time.sleep(0.2)
    keys("{Home}")
    auto.SendKeys(f"{value.month:02d}", waitTime=0.25)
    auto.SendKeys(f"{value.day:02d}", waitTime=0.25)
    auto.SendKeys(f"{value.year:04d}", waitTime=0.25)
    keys("{Tab}")
    shown = get_text(edit)
    if parse_ui_date(shown) != value:
        raise ManualReview(f"{what}: typed {value} but the field shows {shown!r}")


def set_checked(checkbox, checked: bool) -> None:
    """Tick or untick a checkbox, then check the result."""
    pattern = checkbox.GetPattern(P.TogglePattern)
    if (pattern.ToggleState == 1) != checked:
        pattern.Toggle()
        time.sleep(0.3)
        pause()
    if (pattern.ToggleState == 1) != checked:
        raise ManualReview(f"checkbox {checkbox.Name!r} did not change")


def options(combo) -> list[str]:
    """The texts a drop-down offers (Fakturama pads some with a trailing space)."""
    combo.GetPattern(P.ExpandCollapsePattern).Expand()
    time.sleep(0.5)
    names = [c.Name.strip() for c, _depth in auto.WalkControl(combo, maxDepth=4)
             if c.ControlTypeName == "ListItemControl"]
    combo.GetPattern(P.ExpandCollapsePattern).Collapse()
    return names


def choose(combo, option: str) -> None:
    """Pick an entry of a drop-down the way a user does: open it, click the entry
    (DESIGN.md G19). Then check what the drop-down shows."""
    combo.GetPattern(P.ExpandCollapsePattern).Expand()
    time.sleep(0.5)
    entry = None
    for control, _depth in auto.WalkControl(combo, maxDepth=4):
        if control.ControlTypeName == "ListItemControl" and control.Name.strip() == option:
            entry = control
            break
    if entry is None:
        combo.GetPattern(P.ExpandCollapsePattern).Collapse()
        raise ManualReview(f"option {option!r} is not in the drop-down {combo.Name or ''!r}")
    scroll = entry.GetPattern(P.ScrollItemPattern)
    if scroll:  # long lists (e.g. 251 countries): bring the entry into view first
        scroll.ScrollIntoView()
    click(entry)
    time.sleep(0.3)
    if get_text(combo).strip() != option:
        raise ManualReview(f"chose {option!r} but the drop-down shows {get_text(combo)!r}")


# ---------- tabs, menus, saving ----------

def select_tree_item(root, name: str) -> None:
    """Select an item of a native tree, e.g. the category "Orders" in Data > Documents."""
    item = find(root, "TreeItemControl", name, depth=14)
    item.GetPattern(P.SelectionItemPattern).Select()
    time.sleep(1)
    pause()


def tab_names(window) -> list[str]:
    """Names of all editor/view tabs. A leading '*' means unsaved changes."""
    return [c.Name for c, _depth in auto.WalkControl(window, maxDepth=10)
            if c.ControlTypeName == "TabItemControl"]


def switch_to_tab(window, tab_name: str) -> None:
    """Bring an editor tab to the front (its SelectionItem pattern doesn't work).
    The name may carry a '*' (unsaved changes) in front."""
    tab = find_where(window, lambda c: c.ControlTypeName == "TabItemControl"
                     and c.Name.lstrip("*") == tab_name, f"tab {tab_name!r}", depth=12)
    click(tab)
    time.sleep(0.5)


def open_menu(window, menu: str, item: str) -> None:
    """Choose a menu entry, e.g. Data > VATs.

    The menu sometimes opens late or not at all. Clicking the menu title again
    would close a menu that is just opening, so each failed try is undone with
    Escape and the next one starts from a closed menu."""
    entry = auto.MenuItemControl(searchDepth=4, Name=item)
    for attempt in range(3):
        window.SetActive()
        top = find(window, "MenuItemControl", menu, depth=4)
        if attempt == 0:
            top.GetPattern(P.ExpandCollapsePattern).Expand()
        else:
            click(top)  # a real click, in case Expand alone did nothing
        if entry.Exists(3):
            entry.GetPattern(P.InvokePattern).Invoke()
            time.sleep(1.5)
            pause()
            return
        keys("{Esc}")  # close whatever half-opened, then try again
        time.sleep(0.5)
    raise ManualReview(f"menu entry {menu} > {item} not found")


def save_active_editor(window, editor_pane) -> None:
    """Move focus once more (so the last field is stored), then press Save."""
    for edit in [c for c, _depth in auto.WalkControl(editor_pane, maxDepth=30)
                 if c.ControlTypeName == "EditControl" and not c.IsOffscreen][:2]:
        edit.SetFocus()
        time.sleep(0.2)
    press(find(window, "ButtonControl", "Save the current contents", depth=8))
    time.sleep(1.5)
    check_no_error_dialog()


def close_active_tab(window) -> None:
    """Close the editor in front, which must be saved already. If Fakturama
    asks to save, something went wrong (we never discard, DESIGN.md G25)."""
    keys("{Ctrl}w")
    time.sleep(1)
    if window.WindowControl(searchDepth=1, Name="Save Parts").Exists(0.5):
        raise ManualReview("the editor still had unsaved changes when closing it")


# ---------- safety and evidence ----------

WARNING_DIALOGS = {"Internal Error", "Error", "Problem Occurred", "Duplicate Contact"}


def check_no_error_dialog() -> None:
    """Watchdog: stop if Fakturama shows an error or warning dialog.
    They appear either as top-level windows or inside the main window."""
    main = auto.WindowControl(searchDepth=1, SubName="Fakturama -")
    windows = auto.GetRootControl().GetChildren() + (main.GetChildren() if main.Exists(0) else [])
    for window in windows:
        if window.ClassName == "#32770" and window.Name in WARNING_DIALOGS:
            message = " ".join(c.Name for c, _depth in auto.WalkControl(window, maxDepth=4)
                               if c.ControlTypeName == "TextControl" and c.Name)
            raise ManualReview(f"Fakturama showed '{window.Name}': {message[:300]}")


def screenshot(window, path: Path) -> Path:
    """Save a picture of the whole Fakturama window as evidence."""
    window.CaptureToImage(str(path))
    return path


# ---------- reading custom-drawn tables ----------
#
# Fakturama's custom-drawn tables (NatTable): UIA sees an empty box, no rows.
#
# - Read rows: Fakturama's own copy (focus, Ctrl+A, Ctrl+C) gives exact,
#   tab-separated values (DESIGN.md G6).
# - But copying an EMPTY table crashes Fakturama (G7), so first OCR the table
#   to ask "is there any row?".
# - Select a row or edit a cell: click where OCR sees that row's text or that
#   column's header, on a fresh screenshot (G20, G22). Positions are computed
#   at runtime, never written in code.

def park_mouse(control) -> None:
    """Move the mouse onto the title bar of the window that holds `control`.

    The mouse stays where the last click left it, e.g. on the 'select a
    product' icon, and that icon's tooltip then covers the last item line
    (DESIGN.md G29). On the title bar there is no tooltip. The spot comes from
    the window's UIA rectangle right now."""
    window = control.GetTopLevelControl() or control
    rect = window.BoundingRectangle
    auto.SetCursorPos(rect.left + rect.width() // 2, rect.top + 15)
    time.sleep(0.4)  # let the tooltip disappear


def table_in(root):
    """The custom-drawn table under `root`: the biggest pane that has nothing
    inside it except, at most, its scroll bars and the little in-place editor
    a cell leaves behind after editing."""
    candidates = []
    for control, _depth in auto.WalkControl(root, maxDepth=30):
        if control.ControlTypeName != "PaneControl":
            continue
        kinds = {child.ControlTypeName for child in control.GetChildren()}
        if kinds <= {"ScrollBarControl", "EditControl", "ComboBoxControl"}:
            r = control.BoundingRectangle
            candidates.append((r.width() * r.height(), control))
    if not candidates:
        raise ManualReview("could not find a table")
    return max(candidates, key=lambda pair: pair[0])[1]


def table_has_rows(table, shot_path: Path) -> bool:
    """OCR the table's rectangle: is there any text below the header row?"""
    park_mouse(table)
    table.CaptureToImage(str(shot_path))
    lines = read_lines(shot_path)
    if not lines:
        return False
    header_bottom = lines[0].y + lines[0].height
    return any(line.y > header_bottom + 2 for line in lines)


def copy_table(table) -> list[list[str]]:
    """All rows via Fakturama's own copy. Only call it when table_has_rows()
    said yes. The user's clipboard is put back afterwards."""
    saved = auto.GetClipboardText()
    auto.SetClipboardText("")
    table.SetFocus()
    time.sleep(0.3)
    keys("{Ctrl}a")
    keys("{Ctrl}c")
    time.sleep(0.3)
    text = auto.GetClipboardText()
    auto.SetClipboardText(saved)
    return [line.split("\t") for line in text.splitlines() if line.strip()]


def match(wanted: str, seen: str) -> float:
    """How well `wanted` appears in the OCR word `seen`: 1.0 = exactly.

    OCR may misread a letter or two, and RapidOCR sometimes reads a whole row
    as one word ("Northstaroffic...10117"), so `wanted` is also compared with
    every piece of a longer word. A piece scores a little less than a word of
    exactly that length, so an exact word wins a tie."""
    wanted, seen = wanted.lower(), seen.lower()
    if len(seen) <= len(wanted):
        return SequenceMatcher(None, wanted, seen).ratio()
    return 0.99 * max(SequenceMatcher(None, wanted, seen[i:i + len(wanted)]).ratio()
                      for i in range(len(seen) - len(wanted) + 1))


def click_text(table, text: str, shot_path: Path, double: bool = False) -> None:
    """Click on a word in the rows of the table (below the header row)."""
    park_mouse(table)
    table.CaptureToImage(str(shot_path))
    rect = table.BoundingRectangle
    lines = read_lines(shot_path)
    header_bottom = lines[0].y + lines[0].height if lines else 0
    score, word = max(((match(text, w.text), w)
                       for line in lines if line.y > header_bottom + 2 for w in line.words),
                      key=lambda pair: pair[0], default=(0, None))
    if word is None or score < 0.75:
        raise ManualReview(f"could not see {text!r} in the table to click it")
    x = int(rect.left + word.x + word.width / 2)
    y = int(rect.top + word.y + word.height / 2)
    auto.Click(x, y, waitTime=0.05 if double else 0.5)
    if double:
        auto.Click(x, y, waitTime=0.8)
    pause()


def click_only_row(table, shot_path: Path) -> None:
    """Click the table's one and only row. For a search narrowed to a unique
    value (a customer number), so the row's text doesn't have to be read:
    selected rows are drawn in a style OCR reads badly (DESIGN.md G28).
    Stops if OCR sees no row, or more than one."""
    park_mouse(table)
    table.CaptureToImage(str(shot_path))
    lines = read_lines(shot_path)
    header_bottom = lines[0].y + lines[0].height if lines else 0
    rows = [line for line in lines if line.y > header_bottom + 2]
    if not rows:
        raise ManualReview("could not see the row to click it")
    if max(line.y for line in rows) - min(line.y for line in rows) > max(line.height for line in rows):
        raise ManualReview("expected exactly one row in the table, but OCR sees several")
    widest = max(rows, key=lambda line: sum(w.width for w in line.words))
    rect = table.BoundingRectangle
    auto.Click(int(rect.left + widest.x + sum(w.width for w in widest.words) / 2),
               int(rect.top + widest.y + widest.height / 2), waitTime=0.5)
    pause()


def column_position(table, column_header: str, shot_path: Path) -> tuple[float, float]:
    """Where a column is: (x of the middle of its header, bottom of the header
    row), as OCR sees them now. Call it BEFORE editing a row: while a cell is
    being edited, the light-gray header can't be read reliably (DESIGN.md G30)."""
    park_mouse(table)
    for _attempt in range(3):
        table.CaptureToImage(str(shot_path))
        lines = read_lines(shot_path)
        if lines:
            header_bottom = lines[0].y + lines[0].height
            headers = []  # single words, plus pairs of neighbouring words ("Item No.")
            for line in lines:
                if line.y <= header_bottom:
                    headers += line.words
                    headers += [OcrWord(f"{a.text} {b.text}", a.x, a.y, b.x + b.width - a.x, a.height)
                                for a, b in zip(line.words, line.words[1:])]
            score, header = max(((SequenceMatcher(None, column_header.lower(), w.text.lower()).ratio(), w)
                                 for w in headers), key=lambda pair: pair[0], default=(0, None))
            if header is not None and score >= 0.75:
                return header.x + header.width / 2, header_bottom
        time.sleep(0.5)
    raise ManualReview(f"could not find the column {column_header!r}")


def click_cell(table, column: tuple[float, float], shot_path: Path) -> None:
    """Click the cell of the LAST row in a column found by column_position().
    x: from the column; y: the lowest text row on a fresh screenshot."""
    x, header_bottom = column
    park_mouse(table)
    table.CaptureToImage(str(shot_path))
    rows = [line for line in read_lines(shot_path) if line.y > header_bottom + 2]
    if not rows:
        raise ManualReview("could not find the last row of the table")
    last = max(rows, key=lambda line: line.y)
    rect = table.BoundingRectangle
    auto.Click(int(rect.left + x), int(rect.top + last.y + last.height / 2), waitTime=0.5)
    pause()


def click_first_decimal_in_last_row(table, shot_path: Path) -> None:
    """Click the first number like '1.00' in the last row: the Qty cell.

    OCR doesn't read the short 'Qty.' header, but a new line always shows its
    quantity as the leftmost decimal number (the row number has no decimals).
    The number must also sit left of the 'Item No.' header: anything to its
    right is another column (Discount, ...), and is never clicked.
    """
    park_mouse(table)
    number = None
    for _attempt in range(4):  # a just-added row may not be painted (or readable) yet
        table.CaptureToImage(str(shot_path))
        lines = read_lines(shot_path)
        header_bottom = lines[0].y + lines[0].height if lines else 0
        rows = [line for line in lines if line.y > header_bottom + 2]
        item_no = next((line for line in lines if line.y <= header_bottom
                        and line.text.lower().startswith("item")), None)  # the "Item No." header
        if rows and item_no:
            last_y = max(line.y for line in rows)
            words = sorted((w for line in rows if abs(line.y - last_y) < 6 for w in line.words),
                           key=lambda w: w.x)
            number = next((w for w in words if re.fullmatch(r"\d+[.,]\d+", w.text)
                           and w.x + w.width < item_no.x), None)
        if number is not None:
            break
        time.sleep(0.7)
    if number is None:
        raise ManualReview("could not find the quantity of the last item line")
    rect = table.BoundingRectangle
    auto.Click(int(rect.left + number.x + number.width / 2),
               int(rect.top + number.y + number.height / 2), waitTime=0.5)
    pause()
