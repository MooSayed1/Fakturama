# Fakturama UI map (what UIA can and can't see)

Findings from inspecting Fakturama 2.2.0 on Windows 10 with Microsoft UI Automation, before writing any automation code. They ground section 5 of the [design](DESIGN.md).

Legend: ✅ visible and usable through UIA · ⚠️ visible but needs a workaround · ❌ not visible to UIA

## General findings

| # | Finding | Consequence for the design |
|---|---|---|
| G1 | Fakturama is an Eclipse/SWT app. Most controls are native Win32 controls (`Edit`, `Button`, `ComboBox`, `Static`) with readable **Names** taken from their labels. | ✅ Find controls by **ControlType + Name**. |
| G2 | **AutomationIds are random numbers** that change on every start. | Never use AutomationId. |
| G3 | Some inputs have **no Name** (e.g. No., Date, First/Last name, ZIP/City, the Gross/Net combo). Their label is a separate `Text` control placed just before them in the tree. | ⚠️ "Label anchoring": find the label, then take the next input of the expected type after it. |
| G4 | Text fields support **ValuePattern** (set and read text), buttons support **InvokePattern**, checkboxes support **TogglePattern**. | ✅ Act through patterns instead of the mouse, then read the value back. |
| G5 | **Tables are custom-drawn** (Eclipse NatTable). UIA shows one empty pane with no rows or cells. This applies to the search dialogs, the Order's item list and the Data lists. | ❌ Rows can't be read with UIA. See G6. |
| G6 | The tables support **copy**: focus the table (UIA `SetFocus`), press Ctrl+A, then Ctrl+C. Every row lands on the clipboard as tab-separated text with raw values (numbers like `100.0`, full dates). | ✅ Read rows exactly, with no OCR errors. |
| G7 | **Ctrl+C on an empty table crashes**: Fakturama shows an "Internal Error" dialog (`NullPointerException ... "this.copiedCells" is null`). | ⚠️ Check first whether the table has rows (OCR on the table's rectangle) and copy only if it does. |
| G8 | Error dialogs are top-level windows named **"Internal Error"** (class `#32770`), and their text contains the exception. | ✅ A watchdog can detect them by name and stop the run. |
| G9 | Dialogs (e.g. "Select the address") appear as **child windows of the main window**. While a dialog is open, clicks on the main window are ignored. | Search for dialogs under the main window, and always close them (OK or Cancel). |
| G10 | Editors open as **tabs**, named after the document (`PO000001`, `INV000001`, `New Debtor`). `SelectionItem.Select()` fails on these tabs. | ⚠️ Switch tabs by clicking the tab item's UIA rectangle (computed at runtime). |
| G11 | Some icons (next to "Addresses" and "Items") are `Image` controls with **no Name and no InvokePattern**. | ⚠️ Find them by order next to their label and click the center of their UIA rectangle. |
| G12 | When the window is **minimized**, the tree exists but every rectangle is off-screen. uiautomation's `Restore()` didn't restore it; Win32 `ShowWindow(SW_RESTORE)` did. | Restore and focus the window before any click. |
| G13 | The UI uses the **en-US locale**: dates show as `Nov 20, 2026` and money as `$100.00`. | Convert dates to the UI format when typing, and compare money as numbers rather than as text. |
| G14 | **Windows' built-in OCR engine** (`Windows.Media.Ocr`, en-US) is installed. Tesseract isn't. | Used at first: nothing extra to install. Replaced by RapidOCR, which reads faint and noisy text better (decision 19). |
| G15 | The **search boxes** accept `SetValue`, and the list filters correctly, but reading their value back returns `''`. | For these boxes, verify the effect (the filtered rows) instead of the read-back. |
| G16 | An **open editor tab can show old data**: an invoice tab showed *paid* unticked while Data > Documents said *paid*. | Final verification reads the Documents list, not an editor that was already open. |
| G17 | Text set through `ValuePattern` is **sometimes not stored**. The Company field and the money fields showed the text but saved nothing. Real keystrokes are always stored. | ⚠️ Type every value key by key, then read it back. |
| G18 | **Date fields** are edited one part at a time. Home goes to the month, and each part jumps to the next after two digits, so the arrow keys skip a part. | Type `MM`, `DD`, `YYYY` after Home, then check the shown date. |
| G19 | **Drop-downs**: picking an entry through UIA alone isn't reliable. Some entries have a trailing space. | Open the list, click the entry, then read the value back. |
| G20 | In the **search dialogs**, a row counts as selected only after a real click. Ctrl+A and the arrow keys don't select it. | ⚠️ OCR finds the row's text on a fresh screenshot, and the program clicks there. |
| G21 | The **product dialog adds the product by itself** when the search leaves exactly one match. | Always check the Item No. of the line that was just added. |
| G22 | **Item lines** are edited by clicking a cell, typing, then Enter. OCR reads the "Discount" header, but not the short "Qty." header. An edited cell leaves a hidden in-place editor inside the table. | Find the Discount cell by its header and the Qty cell by its value (`1.00`). The table finder tolerates that leftover editor. |
| G23 | An open **Debtor editor doesn't refresh** its payment drop-down after a new payment method is saved. | Resolve the payment method before opening the Debtor editor. |
| G24 | Saving a contact whose name and street already exist shows a **"Duplicate Contact"** warning. | The watchdog stops the run. |
| G25 | The **"Save Parts"** prompt (closing an unsaved editor) can't be reliably unticked through UIA or the keyboard, so OK may save. | The program never closes an unsaved editor. |
| G26 | Windows OCR's async calls **deadlock** in the UI Automation thread (a single-threaded COM apartment). | Applied to Windows OCR (no longer used); RapidOCR has no such problem. |
| G27 | Debtor addresses print the ZIP with a country prefix (`DE-10117 Berlin`). The Order shows only the *Invoice address* tab. | Address checks accept the prefix. |
| G28 | After a table copy (Ctrl+A), the rows **stay selected**: small bold italic text on a blue background. Scaled to 2000 px, OCR read `Northstar` as `star` there. | Windows OCR needed screenshots scaled to 4000 px. RapidOCR reads the text but drops spaces there (`Northstaroffic...10117`), so the Debtor's row, after narrowing the search to one customer number, is clicked as "the only row" without reading it. |
| G29 | The mouse stays where the last click left it. On the "select a product" icon, its **tooltip** ("Pick an item from the list of all products") covers the Qty cell of the last item line, so OCR took the Discount's `0.00` as the quantity. | Before every OCR screenshot, the mouse moves to the window's title bar. The qty cell must also be left of the "Item No." header. |
| G30 | Pressing Enter in an item cell saves it, then opens the **first row's** Qty cell for editing. The column headers are light gray, and in that state OCR sometimes can't read them, so the Discount column couldn't be found. | The Discount column is located before the line is edited; afterwards only the last row's position is read again. |
| G31 | Choosing a category in the **tree left of Data > Documents** (e.g. Invoices > unpaid) filters the list, and the label before "Search:" shows it. Fakturama **remembers** the category, even after the tab is closed, and the tree has no "All" entry. A filtered list hides documents, so the duplicate check and the final verification would be blind. | Every lookup first selects the category it needs, **Orders** or **Invoices** (through UIA, which Fakturama follows), and checks the label. A parent category also shows its subcategories. |

## Screens

### Main window: `Fakturama - <workspace path>`

| Control | Type | How to find | |
|---|---|---|---|
| New Order | Button | Name `Create: New Order` | ✅ Invoke |
| New Invoice | Button | Name `Create: New Invoice` (not used: the Invoice must come from the Order) | ✅ |
| New product | Button | Name `Create a new product` | ✅ Invoke |
| New contact | SplitButton | Name `Create a new contact` → opens "New Debtor" | ✅ Invoke |
| Save | Button | Name `Save the current contents` | ✅ Invoke |
| Menus | MenuItem | `File`, `Edit`, `Data`, `New`, `Extra`, `Window`, `Help` | ✅ |

### Order editor (tab `PO…`)

| Control | How to find | |
|---|---|---|
| No. | first `Edit` after label `No.` | ⚠️ G3 |
| Date | first `Edit` after label `Date` | ⚠️ G3, G13 |
| Gross / Net | the unnamed `ComboBox` in the header row | ⚠️ G3 |
| Cust.Ref. | `Edit` named `Cust.Ref.` | ✅ |
| VAT mode | `ComboBox` named `VAT` (e.g. `With VAT`) | ✅ |
| Select existing address | icon 1 of 2 next to label `Addresses` | ⚠️ G11 |
| Invoice/delivery address text | `Edit` inside the tab `Invoice address` (multi-line value) | ✅ read-back |
| Select product | icon 1 of 4 next to label `Items` | ⚠️ G11 |
| Item lines | custom-drawn table | ❌ G5 (whether copy works here is still to be checked) |
| Discount, Shipping, Total Gross, VAT, Total | named `Edit` / `ComboBox` controls | ✅ |
| Follow-up documents | buttons `Confirmation`, `Invoice`, `Delivery`, `Proforma` in group `Create a follow-up document` | ✅ Invoke |

### "Select the address" and "Select a product" dialogs

| Control | How to find | |
|---|---|---|
| Search | `Edit` after label `Search:`. Setting it through ValuePattern filters the list. | ✅ |
| Results | custom-drawn table | ❌ G5, read with G6 after the G7 check |
| OK / Cancel | named buttons | ✅ Invoke |

Copied row formats:
- Address dialog: `No. ⇥ First Name ⇥ Name ⇥ Company ⇥ ZIP ⇥ City ⇥ …` (three more columns, still to be named)
- Product dialog: `Item No. ⇥ Name ⇥ Description ⇥ Stock ⇥ Price ⇥ VAT`. The VAT column holds a Java object dump that includes `name: [...]` and `taxValue: [...]`.

### Debtor editor (tab `New Debtor`)

- Named `Edit`s ✅: `Customer ID` (pre-filled, e.g. `CUST000002`), `Company`, `Street`, `E-Mail`, `Telephone`, `Mobile`, and so on.
- `ComboBox` `Country` ✅. It defaults to `United States`, so it must be changed.
- Unnamed ⚠️ G3:
  - Salutation combo (default `---`)
  - First name and last name: two `Edit`s after the label `First Name Last Name`
  - ZIP and city: two `Edit`s after the label `ZIP - City`
  - Address type: an `Edit` plus a button after the label `address type`
- Tabs `Addresses`, `Miscellaneous`, `Notice`. Main-address sub-tab, with a `+` button that adds another address.

### Invoice editor (tab `INV…`)

- Same header as the Order, plus:
  - `Service date` and `Order Date` (unnamed `Edit`s after their labels)
  - follow-ups `Delivery`, `Invoice Correction`, `Dunning`
- Checkbox `paid` ✅ (Toggle).
- Payment method: unnamed `ComboBox` (e.g. `Pay Cash`). `Due Days` and `Pay Until` sit next to it.

### Data > Documents

- Left tree (`SysTreeView32`) ✅ UIA-visible: `This transaction`, the customer, `Invoices`, `Orders`.
- Search box ✅, plus the custom-drawn table, read with G6. A row looks like this:

```
ICON_ORDER ⇥ PO000001 ⇥ Fri Nov 20 00:00:00 EET 2026 ⇥ <name> ⇥ <cust.ref or null> ⇥ COMMAND_ORDER_PENDING ⇥ 100.0 ⇥ null
```

State codes seen so far: `COMMAND_ORDER_PENDING` means *open*, `COMMAND_CHECKED` means *paid*.

### More screens (inspected while building)

- **Data menu:** `Documents`, `Products`, `Debtors`, `terms of payment`, `VATs`, …
- **VAT editor:** named `Name`, `Description`, `VAT code (E-Invoice)` (default `S (Standard rate)`), and `Value` (`0%`, a formatted field).
  - The VATs list copies as `standard? ⇥ Name ⇥ Description ⇥ value (0.19)`. It has no code column, so an existing VAT is opened to read its code.
- **Payment editor:** named `Name`, `Description`, `Cash discount`, `Discount Days` and `Net Days`.
  - The payment code drop-down is literally named `!editorPaymentPaymentcode!` (a missing translation key).
  - The payments list copies as `… ⇥ Name ⇥ Description ⇥ discount ⇥ days ⇥ days`.
- **Product editor:**
  - Named: `Item Number`, `Name`, `Description`, `Stock`, and a `VAT` drop-down that lists VAT *descriptions*.
  - `Price (gross)` and `cost price (net)` are formatted fields with no name, after their labels.
- **Debtor editor:**
  - The address type is a small popup with the checkboxes *Invoice address* and *Delivery address*. It opens from the `>` button after the label `address type`.
  - `+` adds the tab `additional address #1`.
  - The Miscellaneous tab has `Alias name`, `Payment` (lists payment *descriptions*), `Discount` and `Net or Gross`.
- **Order item lines** copy as `Qty ⇥ Item No. ⇥ Picture ⇥ Name ⇥ Description ⇥ VAT (object dump with taxValue) ⇥ U.Price (USD 250) ⇥ Discount (-0.1 = 10 %) ⇥ Price`.
- **Invoice, after ticking *paid*:** the payment drop-down (the first drop-down after *paid*), then label `at` with the date, then `Value` with the amount.
