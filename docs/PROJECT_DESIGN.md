# Chemical Etching Works — ERP
## Project Design & Detail Document

> **What this document is.** A single source of truth for the redesigned order-to-dispatch
> workflow shown in the new mockups (login → quotation → design → production → dispatch).
> It defines the flow, roles, screens, data model and every decision taken.

> **Build strategy (decided): FRESH REBUILD.** The new system is written **from scratch** in a
> clean structure (§11) — the old code is **not reused**. The **old system stays live and
> untouched** (the shop uses it daily) until the new one is fully built; at **cut-over** we
> import **masters + current inventory** from the old DB (see §6). References to old models in
> this document are as **data-import sources**, not code to keep.

- **Stack:** Django, SQLite, server-rendered templates (same stack, new codebase).
- **Old apps (live, untouched until cut-over):** `users`, `quotation`, `order_booking`, `transactions`, `store`.
- **Status of mockups:** UI concepts only (static HTML + 2 PDFs) describing the target experience.

---

## 1. The End-to-End Workflow

This is the pipeline the whole system serves. Each stage has an **owner role**, a **screen**, and a **status/handoff** to the next stage.

```
 [Sales]        [Sales Mgr]      [Sales]         [Accounts]        [Design]        [Prod Mgr]     [Floor]      [QA]
   │                │               │                │                │               │             │           │
1.Create   →  2.Approve/    →  3.Send to    →  4.Confirm PO   →  5.Design    →  6.Prioritise → 7.Job    →  8.QA +
  Quotation      reject           customer +      + convert to      + pick sheet    & schedule     card         docs
  (auto-priced)  (comment,        upload PO       SO + move to      + reserve       to floor       station      + retrieval
                 no analytics)    (My Quotations) Design           (manual)                       by station
   │                │               │                │                │               │             │           │
QUOTE_        APPROVED /       AWAITING_PO →   PO_CONFIRMED →     IN_DESIGN →    IN_PRODUCTION → COMPLETED → QA_PASSED →
SUBMITTED     REJECTED→revise  PO_UPLOADED     SO minted,         DESIGN_DONE    (scheduled)     (stations)  DISPATCH
              (v2)                             order booked
```

> **Approval stays; only the analytics is gone.** The Sales Manager still Approves / Rejects a quote
> with a **free-text comment** (rejected → revise & resubmit as v2). What is **out of scope** is the
> automated **cost / benchmark / margin red-amber-green analytics** (the "margin dashboard").
>
> **PO → SO handoff:** Sales uploads the PO on My Quotations; the job then goes to **Accounts**, who
> **confirm the PO** and **convert it to a Sales Order (SO minted) + move it to the Designer.**

**State machine (proposed canonical statuses for one job):**

| # | Status | Set by | Meaning | Screen where it's visible |
|---|--------|--------|---------|---------------------------|
| 0 | `QUOTE_DRAFT` | Sales | Quotation being built | Quotation form |
| 1 | `QUOTE_SUBMITTED` / `PENDING_APPROVAL` | Sales | Submitted, waiting for manager | My Quotations / Manager Approvals |
| 2 | `QUOTE_APPROVED` / `QUOTE_REJECTED` | Sales Manager | Approve, or reject with comment → revise & resubmit (v2) | Manager Approvals / My Quotations |
| 3 | `SENT_TO_CUSTOMER` / `AWAITING_PO` | Sales | Approved quote sent; waiting for customer PO | My Quotations |
| 4 | `PO_UPLOADED` | Sales | PO (+ optional drawing) uploaded | My Quotations |
| 5 | `PO_CONFIRMED` → order booked, **SO minted** / `PO_REJECTED` → back to Sales | Accounts | Accounts verify PO → convert to SO, move to Design. **If PO is wrong: reject with reason → salesperson re-uploads corrected PO and resends** | Order Booking / My Quotations |
| 6 | `IN_DESIGN` (Not started / In progress / Query raised) | Design | Drawing prep + manual sheet pick | Design Queue / Design Job |
| 7 | `DESIGN_APPROVED` + `MATERIAL_RESERVED` | Design | Sheet chosen & reserved from Stores | Design Job → Stores |
| 8 | `READY_TO_PRODUCE` | (system) | Drawing approved **and** material issued | Production dashboard (Ready Queue) |
| 9 | `IN_PRODUCTION` | Production Mgr | Priority 1–4 set, sent to floor | Job Card / Order Booking |
| 10 | `PRODUCTION_COMPLETED` | Floor | All applicable stations done | Job Card → Completed list |
| 11 | `QA_PASSED` / `QA_HOLD` — **DEFERRED (build later)** | QA | Inspection recorded. *Until the QA module is built, completed jobs go straight to Dispatch.* | QA module (future phase) |
| 12 | `DISPATCH_READY` / `DISPATCHED` | Stores/Accounts | Packed & shipped | Order Booking dashboard |

### 1.1 Identity lifecycle — QTN → SO *(confirmed decision)*

A job carries **two identities at different phases of its life**:

1. **QTN (Quotation Number)** — assigned **at the moment the quotation is created** by Sales.
   Everything from quote creation → manager approval → "awaiting PO" → Sales uploading the PO
   is tracked under the **QTN only**. There is **no SO yet** at this point.
2. **SO (Sales Order Number)** — assigned when **Accounts confirm the PO and convert the quote to a
   Sales Order**. Flow: Sales uploads the PO (+ optional drawing) on My Quotations (§3.3) → the job
   goes to **Accounts** → Accounts **verify the PO** → Accounts **convert to SO** (a new SO number is
   stamped and the Order Booking record created) → **move to the Designer**. Either:
   - **PO + drawing uploaded**, *or*
   - **PO only** (drawing to follow).

```
Quotation created ──► QTN-0417   (quote / approval / awaiting-PO / PO-uploaded all live under QTN)
        │  Sales uploads PO (+drawing)
        ▼
Accounts confirm PO ──► "Convert to Sales Order" + move to Design
        │
        ▼
Order Booking record created ──► SO-1035  (SO linked back to QTN-0417 for the rest of its life)
```

- **1 quotation = 1 SO, multi-item (decided).** A quotation can contain **multiple line items**;
  when converted, they all live under **one SO**. Inside the SO, **each item gets its own design
  job, job card (`JC-0001`, `JC-0002`, …) and QA record** — each item has its own artwork,
  material and stations. The SO is complete when **all** its items are complete.
- **Accounts reject path (decided):** if the PO doesn't match the quote, Accounts **reject it with a
  reason**; the salesperson sees this on My Quotations, **re-uploads the corrected PO and resends**.
- **The SO record must store its originating QTN** (foreign key / reference) so we can trace
  an order back to the quote, its pricing, and **the salesperson who raised it** (used by the
  Query module — §3.5/§3.6).
- Screens **before** the SO exists (quotation form, My Quotations list) show **QTN**.
  Screens **from Order Booking onward** (booking, design, production, stores, QA) show **SO**
  and may also display the QTN as a reference (the mockups already show "Ref QTN-0418").
- **Order type New vs Repeat** is entered by the **salesperson at PO upload** (§3.3) and carried into
  the SO: `new` ⇒ drawing expected (matches existing `order_booking.save()` auto-setting
  `is_drawing_avaiable=True` for new).

---

## 2. Roles & Access

The login mockup shows 5 role tabs, but the final system has **8 roles** (decided below). The login
screen's role tabs must be updated to match.

| Role | `User` flag | Screens they own | Status |
|------|-------------|------------------|--------|
| Sales | `is_salesman` | Quotation form, My Quotations list (PO upload) | ✅ exists |
| **Sales Manager** | **new `is_sales_manager`** | Manager Approvals (approve/reject with comment — **no analytics**) | ✅ add (decided) |
| Design | `is_designer` | Design Queue, Design Job Detail | ✅ exists |
| **Production Manager** | **new `is_production_manager`** | Production dashboard (Ready Queue) — **decides which SO goes to the floor**, sets priority 1–4, sends to production | ✅ add (decided) |
| **Production Floor Manager** | **new `is_floor_manager`** | Job Card — **starts the job, selects which stations apply, marks each step complete** | ✅ add (decided) |
| **Stores** | **new `is_stores`** | Stores dashboard — log sheets, reserve, issue material | ✅ add (decided) |
| Accounts | `is_accounts` | Order Booking dashboard — **confirm PO + convert to SO + move to Design** | ✅ exists |
| **QA** | **new `is_qa`** | QA module (new) | ✅ add |

> The two production roles are **distinct** (confirmed): the **Production Manager** picks which sales
> orders move to the floor and their priority; the **Production Floor Manager** runs the job on the
> floor — starts it, chooses the applicable stations, and marks each step done. The old unused
> `is_planner` / `is_executor` flags can be retired or mapped onto these two.

**Access rules (enforced server-side, not just hidden in UI):**
- Design Queue: *"Order value and commercial details are not visible in this view."* → Design role must be **blocked** from price fields.
- **Login = username + password only. OTP is NOT required (dropped).**

---

## 3. Screen-by-Screen Specification

For each screen: **who uses it, what it shows, where each field comes from, what actions it triggers, and the backing model.**

### 3.1 Login (`login page.html`)
- **Role:** all. **Purpose:** authenticate + pick role context.
- **Fields:** Employee ID/Email, Password, Role tabs, "Keep me signed in", OTP option.
- **Source:** `users.User` (Django auth). Role tabs = the `is_*` flags.
- **To build:** role landing (redirect each role to its own dashboard after login). **No OTP** (dropped) — username + password only.

### 3.2 Quotation (form + `Quotation ... .pdf`)
- **Role:** Sales. **Purpose:** build a priced quotation; the printed PDF is the customer-facing output.
- **Header fields (PDF):** Quotation No., Date, Valid Until, Ref/Enquiry No., Bill-To (customer, GSTIN, contact), Sales Person, Payment Terms, Delivery Terms, Lead Time.
- **Line items:** Description/Artwork Ref (DWG-xxx), Material, Size (mm), Thickness, Finish/Fill,
  **+ batch-wise slab pricing** (see below), Tooling/Screen charges, Sub Total, P&F, Freight,
  GST %, Grand Total.
- **Batch-wise pricing (updated — see `Quotation Batchwise Chemical Etching.pdf`):**
  Each line item has **multiple quantity tiers** with different rates, entered **manually** by
  the salesperson (not auto-calculated from PSI). Example:
  | Batch Qty (Nos.) | Rate / Unit | Est. Amount |
  |---|---|---|
  | 0–100 | USD 2.40 | USD 240.00 (@100) |
  | 101–200 | USD 2.00 | USD 400.00 (@200) |
  | 201–500 | USD 1.80 | USD 900.00 (@500) |
  *"Rate applicable is based on the total ordered quantity for this item (slab pricing).
  Tooling/screen charges, if applicable, are billed separately as one-time."*
  This replaces the old single-rate-per-item model. The **PSI auto-pricing table is NOT used** —
  all rates are typed manually.
- **Source (existing — for reference, not code-reused):**
  - Customer → `quotation.sales_customer` (name, address, gst, credit_limit).
  - Quote + totals → `quotation.order` (numbering, dates, payment/delivery terms).
  - Line items → `quotation.order_child` (material, length/width, thickness, finish options).
  - ~~**Auto-pricing** → `quotation.PSI`~~ — **dropped; pricing is fully manual now.**
- **Actions:** Save draft → Submit (quote goes to the **Sales Manager for approval**; it appears on the salesperson's **My Quotations** list, §3.3, as *Pending Approval*).
- ⚠️ **Provenance gaps:** see §5 items on numbering (`QTN-`), Valid Until, GST %, tooling charge.

### 3.3 Sales — My Quotations / Sales Dashboard (`sales dashboard.html`)
- **Role:** Sales. **Purpose:** the salesperson's list of every quotation they've raised, its approval
  status, and the actions that carry it forward (send to customer, upload PO, hand to Accounts).
- **Summary tiles:** Total quotations, Pending approval, Rejected — needs revision, Approved,
  Approved value (this month), Awaiting PO. *(counts — computed.)*
- **Per row:** QTN no. **+ version (v1 / v2 resubmitted)**, customer (+ New/Repeat), material/spec,
  amount, submitted date, **status badge**, contextual **action**.
- **Statuses & actions seen:**
  - **Draft** → *Continue Editing*.
  - **Pending Approval** → *View* (waiting on Sales Manager).
  - **Rejected** (shows manager's free-text comment, e.g. *"Margin too low — revise rate"*) → *Revise & Resubmit* (creates **v2**).
  - **Approved → Awaiting PO** → *Send to Customer* (**auto-emails the quote PDF** to the customer's email on file, then sets *Awaiting PO*), *+ Upload PO / Drawing*.
  - **Approved → PO & drawing received** (shows PO/drawing file chips) → *Convert to Sales Order →* (hands to **Accounts**), *View PDF*.
- **Upload PO captures:** **PO number**, **PO file**, **PO date**, optional **drawing file**,
  **expected delivery date**, **New/Repeat** — all entered by the salesperson.
- **Handoff:** after PO upload, *Convert to Sales Order* sends the job to **Accounts** (§3.4) for PO
  confirmation; **Accounts** (not Sales) actually mint the SO and move it to Design.
- **Backing (existing):** `quotation.order` (`is_approved` / `is_declined` / `is_converted`,
  `rejection_reason`) — this model already supports the whole approve/reject/convert lifecycle.
- ⚠️ **Provenance gaps:** **quotation version (v1/v2)** — needs a version field; **PO fields** — added here (this screen is their origin).

### 3.3b Sales Manager — Quotation Approvals *(approval only — no analytics)*
- **Role:** Sales Manager. **Purpose:** review submitted quotes and **Approve** or **Reject with a
  comment** (which sends it back to Sales as *Rejected — needs revision*).
- **Shows per quote:** customer, salesperson, material/thk, size, qty, rate, grand total.
- **Actions:** Approve · Reject with comment · (Request revision).
- **Backing (existing):** `quotation.order.is_approved` / `is_declined` / `rejection_reason`.
- ❌ **Removed from the original `manager dashboard.pdf`:** the "MANAGER ONLY" **cost / benchmark /
  margin red-amber-green analytics** panel — **out of scope**. Only the plain approve/reject remains.

### 3.4 Order Booking Dashboard (`order booking dashboard.html`)
- **Role:** Accounts. **Purpose:** confirm incoming POs, convert quotes to Sales Orders, and keep
  whole-pipeline visibility across every open order.
- **Key actions (Accounts):** **Confirm PO** on a job Sales handed over → **Convert to Sales Order**
  (mint SO, create the Order Booking record with **all the quote's line items**) → **Move to Designer**
  (into the Design queue, §3.5). This is the step that turns a QTN into an SO (§1.1).
  **Or: Reject PO with reason** → back to the salesperson (My Quotations) to re-upload and resend.
- **Shows:** SO No./Date, Customer (+ New/Repeat, QTN ref), **PO file + Drawing file (with Rev)**, Order Value, **Stage track (5 dots)**, Expected Delivery, Status badge (On track/Held/Delayed/Awaiting drawing), Sales Person.
- **Source (existing):** `order_booking.order_booking` (customer, order_id, status, order_type, delivery_date, design dates, `is_drawing_avaiable`, `is_flim_avaiable`), salesperson via `quotation.order.user`.
- ⚠️ **Provenance gaps:**
  - **PO file & PO number** — ✅ captured at §3.3 (Sales Upload PO); model fields still need adding on `quotation.order` / `order_booking`.
  - **Drawing revision (Rev00/01/02)** — `order_matarial_production_drawing` is a flat file, **no revision/version column**.
  - **5-stage dot track** & **status badges** ("Delayed — material", "Held — design query") — these are *derived* states; the derivation rules must be defined (see §4).
  - **Order Value visible here** but hidden from Design — enforce per-role.

### 3.5 Design Queue (`design queue view.html`)
- **Role:** Design. **Purpose:** designer's worklist of orders at design stage (no prices).
- **Shows per card:** SO no. + received date, customer, New/Repeat, drawing file + **Rev**, stage pill (Not started / In progress / Query raised), *"matches prior order SO-0987 / same spec as last 3 orders"*, Expected Delivery, actions (Start/Continue, Raise Query, Query Thread).
- **Source (existing):** `order_booking.order_booking` filtered to design stage; drawing via `order_matarial_production_drawing`; New/Repeat via `order_type`.
- ⚠️ **Provenance gaps:**
  - **"Query Raised" state + Query Thread** — no query/thread model exists at all.
  - **"Matches prior order SO-0987" / "same spec as last 3 orders"** — repeat-detection/link logic undefined.
  - **Drawing revisions** — same gap as 3.4.

### 3.6 Design Job Detail (`design job detail.html`)
- **Role:** Design. **Purpose:** review drawing, **manually pick a sheet/offcut from Stores**, reserve it, mark design approved. *(No fit/yield nesting calculation — dropped.)*
- **Shows:** spec grid (material, thickness, part size, qty, finish, holes), drawing + **rev history**, **Stores inventory filtered to material+thickness** (a plain selectable list), Design Query box, order type/history, activity log, "Mark Design Approved → Production Queue".
- **Source (existing):**
  - Sheet/offcut list → `store.product_qr` (+ `moved_to_left_over` = offcut), size via `store.size`, location via `store.shelf`/`product_qr_shelf`, supplier via `store.dealer`.
  - Reserve → sets the chosen sheet's status to reserved against this SO (records who + when).
  - Activity log → `transactions.project_logs`.
- ⚠️ **Provenance gaps:**
  - **Drawing rev history** — no revision model.
  - **Design Query** — new model (see §7.1).

### 3.7 Production Dashboard — Ready Queue (`production dashboard.html`)
- **Role:** Production Manager. **Purpose:** see only jobs that are **ready to produce**, set priority, release to floor.
- **Gate (stated):** *"Only orders with drawing approved + material issued appear here."*
- **Shows:** SO no. + Ready chip, customer + QTN ref, New/Repeat, material (+ "new sheet issued" / "leftover OFF-xxxx"), qty, **order value**, **Due In (days + date, colour-coded)**, **Priority (1–4)**, actions (Send to Production, View drawing). *(No summary KPI tiles — dropped.)*
- **Source (existing):** `order_booking.production_orders` (has `priority` int + `stage`), `transactions.project_working_order` (priority + status).
- ⚠️ **Provenance gaps:**
  - **The "Ready" gate** (drawing approved AND material issued) — needs a single computed flag combining `is_drawing_avaiable` + material-reserved state.
  - **"Due In"** — computed from the **expected delivery date** entered by the salesperson at PO upload (§3.3); colour thresholds still to set (§4).
  - **Priority** — ✅ resolved: **numeric 1–4** everywhere (High/Normal/Low dropped).

### 3.8 Job Card — Station Flow (`job card stations.html`)
- **Role:** **Production Floor Manager.** **Purpose:** start a job, **select which stations apply**, then run it station-by-station marking each **Complete** (or Skipped).
- **Flow:** Floor Manager opens the job → **"Start work"** (captures start time) → **ticks the applicable stations** from the Station master → marks each station complete as it's done. Duration per station = gap between marks (auto). When all applicable stations are done → job **Completed** → goes to QA.
- **Shows:** JC no. linked to SO + customer + material UID, spec grid, the **selected station list**, each with **operator + timestamp + duration**, current station highlighted, skipped stations struck through, overall progress %, drawing ref, material source.
- **Source:** Job ↔ order → `order_booking.production_orders`; new **JobCardStation** rows per station; operators → `store.cutter`; material → `store.product_qr`.
- ✅ **Resolved:** station list = **Station master**, chosen **manually by the Floor Manager**; per-station time **auto-computed**; JC number = **`JC-0001`**.
- ✅ **One job card per SO item** (decided): a multi-item SO produces multiple job cards, each running independently through the stations.
- ✅ **No "QC Inspection" station** (decided): QA is a **separate module** after production — quality checks happen there, not as a job-card station.
- ⚠️ **Provenance gaps:** new **JobCardStation** table (per-station status/operator/time) must be built; progress % is computed from it.

### 3.9 Stores — Raw Material Inventory (`stores dashboard.html`)
- **Role:** Stores. **Purpose:** manage sheets & offcuts, issue material, watch reorder levels.
- **Shows:** UID (SHT-/OFF-), material+thk, size, status (Available/Reserved/Consumed), **reserved/consumed against SO + by whom**, **Origin** (Purchased — GRN-xxxx / From SHT-xxxx leftover), Location (Rack), Received date, Issue button; summary (total/available/reserved/consumed/offcuts/below-reorder); **"below reorder level (min: 3) — Purchase Requisition suggested."**
- **Source (existing):** `store.product_qr` (sheet, supplier, `moved_to_left_over`=offcut, `moved_to_scratch`, `date_of_pur`, `finish`, `status`), location via `shelf`/`product_qr_shelf`, leftover lineage via `transactions.material_history` + `left_over_stock`/`scratch_stock`, reserve/consume-against via the nesting link.
- ⚠️ **Provenance gaps:**
  - **UID scheme SHT-/OFF-** — existing uses QR codes + `item_code`, not SHT-/OFF- strings.
  - **GRN (Goods Receipt Note) number** — no GRN model; closest is `transactions.project_inward`.
  - **Reorder level (min: 3) per material** — no `reorder_level` field anywhere.
  - **"Reserved by Meera D. (Design)"** — reservation actor/audit not currently stored on the sheet.

### 3.10 QA Module — **DEFERRED (decided: build later)**
- User is not yet sure of the QA requirements (forms, outcomes). **Not part of the current build.**
- **Interim behaviour:** when all job cards of an SO are completed, the SO goes **straight to Dispatch**.
- The state machine keeps a slot for QA (§1 status 11) so it can be inserted later without rework;
  the proposal in §7.2 stays as the starting point for that future phase.

---

## 4. Derived States & Business Rules That Must Be Defined

These drive badges, colours, and gates across many screens and currently have **no formal rule**:

| Derived thing | Appears on | Rule to define |
|---------------|-----------|----------------|
| "Ready to produce" | Production dashboard | `drawing approved == True` AND `material reserved/issued == True` |
| Status badge (On track / Held / Delayed) | Order Booking | e.g. Held = open query or material PO pending; Delayed = past a checkpoint date; else On track |
| "Due In" colour (crit/soon/ok) | Production dashboard | thresholds in days vs which delivery date field |
| Priority scale | Production + floor | ✅ **Numeric 1, 2, 3, 4** (decided). Production dashboard's High/Normal/Low is dropped in favour of 1–4. |
| New vs Repeat + prior-order link | Design/Booking | how a new order is matched to a prior SO (customer + item_code + spec?) |
| Reorder alert | Stores | `available_qty < reorder_level` per material/thickness |
| Progress % | Job card | done applicable stations / total applicable stations |

---

## 5. ⚠️ Data with No Origin — "Shown on a screen, but never captured anywhere we've seen"

**This is the core list you asked for.** Reading the screens in flow order, these are the values
that **appear on a later screen but are never entered or produced on any earlier screen**. Each
one means we must **add an input, screen, or module** to capture/produce it — otherwise the
screen has nothing real to show.

**Legend:** 🟥 nothing captures it anywhere → new module/screen needed · 🟧 partly captured, but a piece is missing · 🧮 it's a calculation, not an input (needs logic, not a form).

---

### Before the flow even starts — "master" data the screens assume already exists
The quotation screen shows customers, materials, finishes and **rates** as reference data. These
**already exist as tables** in the current models and are reused — maintained via Django admin
(a nicer in-app "masters" screen is optional, not required):

| Shown on | Value | Status |
|----------|-------|--------|
| Quotation | Customer name, GSTIN, address, contact, credit limit | ✅ Exists — `quotation.sales_customer` / `store.customer`. (Optional: a create-customer screen for Sales.) |
| Quotation | Rate / unit | ✅ **Changed: manual entry, not auto-priced.** PSI table dropped. Salesperson types batch-wise slab rates per item (see §3.2). |
| Quotation | Material / thickness / finish / etching lists | ✅ Exists — `store.category` / `thickness` / `grade` / `size` and `quotation.etching` / `color` / `text_matter` / `process`. Maintained via admin. |
| Login | Employees, their role, station operators | ✅ Exists — `users.User` (role flags) + `store.employee` / `cutter`. (New role flags per §2; no OTP.) |

> With the **fresh-rebuild decision**, these become clean new models in the `masters` app, and the
> existing values are **imported from the old DB at cut-over** (§6) — so no re-typing, but the
> models themselves are built new.

---

### Screen 4 — Order Booking Dashboard
| Value shown | Captured on an earlier screen? |
|-------------|--------------------------------|
| **PO file + PO number** | ✅ **Resolved.** Captured on the Sales **My Quotations** list "Upload PO" action (§3.3) — this is also what mints the SO. |
| **Drawing file + Rev (Rev00/01/02, "customer revision")** | 🟥 The quotation only had an artwork *reference* (DWG-001), not the actual **drawing file** or a place to upload **new revisions**. Need a drawing upload + revision screen. |
| **Expected Delivery date** | ✅ **Resolved.** Entered by the **salesperson** on the "Upload PO" action (§3.3). |
| **New vs Repeat** | ✅ **Resolved.** Chosen by the **salesperson** on the "Upload PO" action (§3.3). |
| Stage track (5 dots) + status badge (On track / Delayed / Held) | 🧮 Derived — needs the rules in §4. |

---

### Screens 5 & 6 — Design Queue & Design Job
| Value shown | Captured on an earlier screen? |
|-------------|--------------------------------|
| Drawing + revision history | 🟥 Same missing drawing-upload screen as above. |
| **"Matches prior order SO-0987" / "same spec as last 3 orders" / "Prior Orders: None"** | 🟥 **Never.** Nothing links an order to a customer's earlier orders. Need history/repeat-match logic + a stored link. |
| **Part-level spec: Part Size 80×50, Holes 2×Ø3mm, Drilling** | 🟧 The quotation captured overall size + finish, but **holes/drilling** aren't clearly captured. Where does the hole spec come from? |
| ~~"312 pcs fit / 96% yield" per sheet~~ | ❌ **Not required (dropped).** No fit/yield calculation — the designer just **selects a sheet manually** from the Stores list. |
| ~~Expected leftover → new offcut~~ | ❌ **Not required (dropped).** No auto-nesting/auto-offcut. |
| Design Query / "Query raised" | ✅ Now defined — Query module (§7.1). |
| Sheet/offcut list (material + thickness) | ✅ Comes from Stores (screen 9); designer picks one manually. |

---

### Screens 7 & 8 — Production Ready Queue & Job Card
| Value shown | Captured on an earlier screen? |
|-------------|--------------------------------|
| **The station list (13 stations, "auto-selected", some "skipped as N/A")** | ✅ **Resolved.** A **Station master model** (add / remove / edit stations) will be created; the **Production Floor Manager manually selects which stations apply / are skipped** per job when starting it (no auto-routing from spec). |
| **Per-station operator + timestamp + duration (28 min)** | ✅ **Resolved.** Operator clicks **"Start work"** (captures start time), then **marks each station complete**. Duration for a station = time between the previous completion (or Start) and this completion — **auto-computed, not typed**. |
| **"Ready" chip (drawing approved + material issued)** | 🧮 Derived gate — needs the rule in §4. |
| **Due In (days)** | 🧮 From Expected Delivery — but that date itself isn't captured yet (see screen 4). |
| **Priority** | ✅ **Numeric 1, 2, 3, 4** (decided). Set by the Production Manager here; the floor sees the same 1–4. |
| JC number | ✅ **Format `JC-0001`** (decided) — minted at "Send to Production". |
| ~~Summary KPIs (₹ in queue, due ≤2 days, scheduled today, etc.)~~ | ❌ **Not required (dropped).** No dashboard KPI tiles. |

---

### Screen 9 — Stores
This screen *is* the origin for sheets (it has "Log New Sheet"), but a few shown values still aren't captured:

| Value shown | Captured on an earlier screen? |
|-------------|--------------------------------|
| **Origin col — "Purchased — GRN-2291"** (for bought sheets) | 🟥 Typed by **Stores when the sheet is received** ("Log New Sheet" / inward). Needs a **GRN number field** — not captured today. |
| **Origin col — "From SHT-0771 (leftover)"** (for offcuts) | ✅ **Auto-shown** — it's the **parent sheet the offcut was cut from** (a stored link). Filled when Stores/production logs the leftover after cutting; no typing of the origin itself. |
| **Reorder level (min: 3)** | 🟥 **Never set anywhere.** Need a per-material reorder level input. |
| **"Reserved by Meera D. (Design)"** | 🟧 Comes from the design reserve action — store who + when. |

---

### Screen 10 — QA
❌ **Deferred (decided)** — QA module is not part of the current build; completed jobs go straight to Dispatch. Requirements (forms, outcomes) to be defined when it's taken up (§7.2).

---

### One-line summary of the modules we must ADD to capture the above
1. **Masters already exist** (customer, PSI rates, material/thickness/finish/etching, users) — reused via existing tables / admin. Nothing new needed (OTP dropped).
2. **My Quotations / Sales Dashboard** (§3.3) — approval statuses, **quotation versioning (v1/v2)**, Send-to-Customer, Upload PO, Convert-to-SO handoff.
3. **Manager Approvals** (§3.3b) — plain approve / reject-with-comment (no analytics). Backed by existing `is_approved`/`is_declined`.
4. **Accounts: Confirm PO → Convert to SO → Move to Design** (§3.4) — this mints the SO.
5. **Drawing upload + revision** screen.
6. **Order history / repeat-match** link.
7. **Manual material selection + reserve** on the Design job (no fit/yield calc — dropped).
8. **Station master** (add/remove/edit stations) ✅ decided; per-job stations **manually selected by the Floor Manager** ✅ decided. Per-station time auto-computed from Start + completion marks ✅ decided.
9. **GRN capture + reorder levels** in Stores.
10. ~~QA module~~ — **deferred to a future phase**.

> **Descoped (still):** only the Manager **margin/cost/benchmark analytics** is removed — the plain
> **approve/reject** step stays. Also dropped: nesting fit/yield calc, auto-offcut, dashboard KPI tiles.

---

## 6. Data Model — Fresh Build + Data Import (decided)

**All models are built new and clean** in the new apps (§11). The old DB is only a **data source
at cut-over** — nothing is code-shared with the live system.

### Data imported from the old DB at cut-over (one-time script)
| New app | Imported from old system | What |
|---------|--------------------------|------|
| `masters` | `quotation.sales_customer` / `store.customer` | Customers (name, GST, address, contacts) |
| `masters` | `quotation.PSI` + `etching`/`color`/`text_matter`/`process` | Pricing rates + option lists |
| `masters` | `store.category`/`thickness`/`grade`/`size` | Material / thickness / size lists |
| `users` | `users.User` | User accounts (new role flags assigned manually) |
| `stores` | `store.product_qr` (+ `shelf`, `material_history`) | **Current sheet/offcut inventory snapshot taken on go-live day** |

Old quotations / orders / project **history is NOT migrated** — it stays viewable in the old
system's DB archive.

### New models to build (all new — clean names, no typos)
1. **Customer, Material, Thickness, Finish, Etching, StationMaster, ReorderLevel** — the `masters` app.
   ~~RateCard (PSI)~~ — **dropped; pricing is fully manual / batch-wise now (§3.2).**
2. **Quotation + QuotationItem + QuotationItemPriceTier** — QTN numbering, version (v1/v2),
   approval fields (approved/rejected + comment), PO fields (number, file, date), auto-email log.
   Each item has **multiple price tiers** (batch qty range → rate/unit → est. amount), all manually
   entered by the salesperson.
3. **SalesOrder + SOItem** — SO minted by Accounts, FK → Quotation; **one SOItem per quote line item**; expected delivery, New/Repeat, canonical `status` (state machine §1).
4. **DrawingRevision** — per SOItem: Rev 00/01…, file, source, uploaded_by, is_current (old ones viewable).
5. **DesignQuery / QueryMessage** — routed to the quote's salesperson (§7.1).
6. **MaterialReservation** — SOItem ↔ sheet/offcut, reserved_by/at, issued_by/at (manual pick, no nesting calc).
7. **JobCard + JobCardItem + JobCardStation** — **one JC covers a SUBSET of item codes** (`JC-0001`), via the `JobCardItem` junction; the subset may even span several Sales Orders. A card pulled out of a batch early is flagged `is_urgent_split` and must carry a written `urgent_reason`. Stations are picked manually by the Floor Manager from StationMaster; per-station operator + timestamps, duration auto-computed. No QC station.
8. **Sheet / Offcut** (stores) — UID display, GRN no., rack location, status (available/reserved/consumed), offcut parent link.
9. **Notification + AuditLog** — `core` app, used by every module.
10. ~~QA models~~ — deferred (§7.2).

---

## 7. New Modules — Detailed Design

### 7.1 Design Query Module *(confirmed decision)*

**Rule:** When Design raises a query on a job, it must be answered by **the exact salesperson
who created the original quotation** — not "sales" as a group.

- **Routing source:** the SO's originating QTN → `quotation.order.user` (the salesperson who
  raised that quote). Because the SO stores its QTN reference (§1.1), this link is always available.
- **Where raised:** "Raise Query to Sales" on the Design Job Detail (§3.6) and Design Queue cards (§3.5).
- **Where answered:** the salesperson sees their assigned queries (their inbox) and replies.
- **Effect on job:** raising a query sets the design sub-state to `Query raised`; the job shows
  as **Held — design query** on the Order Booking dashboard until it is resolved.

**Proposed models:**

| Model | Fields |
|-------|--------|
| `DesignQuery` | `order` (FK SO/order_booking), `raised_by` (Design user), `assigned_to` (auto = quotation salesperson), `subject`, `status` (`open` / `answered` / `closed`), `created_at`, `resolved_at` |
| `QueryMessage` | `query` (FK DesignQuery), `author` (user), `body`, `attachment` (optional file), `created_at` |

**Flow:**
```
Designer opens SO ─► "Raise Query to Sales"
        │  (system finds original salesperson via QTN → quotation.order.user)
        ▼
DesignQuery(status=open, assigned_to=that salesperson)  +  first QueryMessage
        │  job → "Query raised" / booking badge "Held — design query"
        ▼
Salesperson replies (QueryMessage)  ─►  status=answered
        ▼
Designer resumes / closes query  ─►  status=closed, job back to In progress
```

> Notifications: reuse existing `transactions.notification_table` / Channels to alert the
> salesperson when a query lands and the designer when it's answered.

### 7.2 Proposed QA Module — **DEFERRED to a future phase (decided)**

Kept here as the starting design for when QA is taken up later:

- **QA Queue** — lists jobs with status `PRODUCTION_COMPLETED`, ordered by completion date; filter by customer/date.
- **QA Inspection screen** — for a job: spec + drawing reference (read-only), then **4–5 structured forms** (e.g. *Dimensional*, *Finish/Colour*, *Etch depth*, *Visual/Cosmetic*, *Packing check*) — exact fields to be confirmed with the QA team. Each form = a set of measured/observed fields + pass/fail + remarks.
- **Document upload** — attach inspection reports, photos, certificates.
- **Outcome** — QA Pass → `QA_PASSED` (moves to Dispatch), or QA Hold → back with reason.
- **Retrieval / reporting** — search all QA records **by date range** and **by customer**, export/print.

➡️ **Open question for you:** the exact 4–5 forms and their fields. Everything else can be built around whatever those turn out to be.

---

## 8. Suggested Build Order (phasing)

1. **Foundation:** unify status/state machine (§1); add missing roles (`is_stores`, `is_qa`, Sales-Manager); role-based landing + access control (hide prices from Design).
2. **Quotation + Approval:** quotation form → PSI pricing; My Quotations list with versioning; Sales-Manager approve/reject-with-comment (no analytics); Send to Customer.
3. **PO → Order Booking:** Sales uploads PO on My Quotations → **Accounts confirm PO → Convert to SO** (mint + numbering) → move to Design; drawing revisions; Order Booking dashboard with status badges.
4. **Design:** design queue, drawing revisions, manual sheet selection + reserve-from-Stores, design query threads (no nesting calc).
5. **Production:** Ready-queue gate, priority/scheduling, **station master + manual station selection + JobCardStation** (biggest new build).
6. **Stores polish:** GRN, reorder levels, reservation audit.
7. ~~QA~~ — **deferred to a future phase** (user not yet sure of requirements). Until then, completed jobs go straight to Dispatch.
8. **Cut-over:** import masters + inventory snapshot from the old DB (§6), assign new role flags to users, train the team, switch the shop to the new system; old system archived read-only.

---

## 9. Cross-Cutting Concerns

- **Audit trail:** every status change / field edit should log (pattern already exists in `transactions.project_logs`). Extend to all stages so "Activity Log" panels are real.
- **Notifications:** `transactions.alert` / `notification_table` / `system_alert` + Channels already present — use for handoffs (e.g. "design done → notify production").
- **File storage:** drawings/PO/QA docs under `media/` (pattern exists) — standardise folders + keep revisions.
- **Email/SMTP gateway (required):** "Send to Customer" auto-emails the quote PDF to the customer. Needs an SMTP/email service configured (credentials + deliverability); gateway cost borne by the client. Customer email comes from the customer master.
- **Numbering:** one central numbering service for all prefixes (QTN/SO per §1.1; JC/SHT/OFF/GRN/DWG per §5).
- **One canonical "job" (✅ settled by the fresh rebuild):** the chain is **`Quotation` → `SalesOrder` → `JobCard`**,
  one record per phase, each linked to the previous. The old three-way overlap
  (`quotation.order` / `order_booking.order_booking` / `transactions.project`) is not carried over,
  so every screen already reads the same record.
- **Out of scope (confirmed):** invoicing, billing and payment tracking. The flow ends at dispatch —
  list this in the client quotation's exclusions.

---

## 10. Review Findings — Missing Pieces & Remaining Open Questions

*(Added after a full-document review, 08 Aug 2026.)*

### 10.1 Structural gaps found in review — need a decision

| # | Gap | Status |
|---|-----|--------|
| 1 | **Multi-item quotations → SO shape** | ✅ **Decided: 1 quotation = 1 SO; the SO contains all the quote's line items.** Each **item** inside the SO gets its own design job, job card (`JC-0001`, `JC-0002`, …) and QA record, since each item has its own artwork/material/stations. The SO is complete when all its items are done. |
| 2 | **Accounts rejects a PO** | ✅ **Decided:** Accounts can **reject the PO with a reason → it goes back to the salesperson** (visible on My Quotations), who **re-uploads the corrected PO and resends to Accounts**. Loop until confirmed. |
| 3 | **Reserved vs Issued.** | ✅ **Decided: two-step.** **Designer marks Reserved** (picks the sheet on the Design Job screen). **Store in-charge marks Issued** (confirms physical handover on the Stores dashboard). The **Ready-queue gate** is: drawing approved **AND** material **issued** (not just reserved). |
| 4 | **QC station vs QA module overlap** | ✅ **Decided: QA is a separate module** after production. The "QC Inspection" station is **removed from the job-card flow** (the Floor Manager simply doesn't include it; QA happens in the QA module). |
| 5 | **Order cancel / hold mid-flow.** | ✅ **Decided: `ON_HOLD` + `CANCELLED` with mandatory reason.** Available from any pre-dispatch status, set by Accounts on the Order Booking dashboard. `ON_HOLD` pauses the job (hides from active queues, shows "Held" with reason on Order Booking; resumable — goes back to its previous status). `CANCELLED` is terminal (job killed, reserved material released back to available). Both logged to audit trail. |
| 6 | **Partial QA rejection.** | 💤 Parked with the deferred QA module — decide when QA is built. |
| 7 | **Invoicing & payment** | ✅ **Decided: NOT in the feature list — out of scope.** The flow ends at dispatch. "Invoicing / billing / payment tracking" must be listed in the client quotation's **exclusions** so it can be added later as a paid change request without dispute. |
| 8 | **Dispatch step ownership.** | ✅ **Decided: two-step.** **QA marks `DISPATCH_READY`** (ready to dispatch) → **Store in-charge marks `DISPATCHED`** (outward done). While QA is deferred, completed jobs go straight to `DISPATCH_READY` and the Store in-charge marks outward. |
| 9 | **Stores: active role or passive DB?** | ✅ **Decided: active.** Store in-charge has their own screens (Stores dashboard from mockup). They log new sheets, mark material as **Issued** (§10.1·3), and mark **outward done** (§10.1·8). The `is_stores` role flag drives access. |

### 10.2 Still-open questions (from earlier rounds)

1. **PO-only path** — ✅ **Decided: both PO AND drawing are required.** Design cannot start without
   a drawing. If only PO is uploaded, the job shows as **"Awaiting drawing"** and does not enter
   the Design queue until the drawing is also uploaded.
2. **Repeat-order link** — 🟡 **Still thinking** — parked for now, decide later.
3. **Drawing revisions** — ✅ **Decided: Sales + Design can both upload revisions.** All revisions
   are retained (old ones stay viewable); the newest is marked `is_current`. See the
   `design job detail.pdf` mockup for the revision history panel layout.
4. **Holes/drilling spec** — ✅ **Decided: entered by the Designer**, not Sales. The quotation
   captures overall size/finish/thickness; holes and drilling details are filled in by Design
   when preparing the drawing.
5. ~~**GRN**~~ — ✅ **Decided: ignored / not building.** No GRN number or GRN field in v1.
6. **Reorder level** — ✅ **Decided: admin defines the benchmark.** Reorder levels are set via
   Django admin per material/thickness, not on the Stores user-facing screen.
7. ~~**Sheet/Offcut UID**~~ — ✅ **Decided: ignored.** No `SHT-`/`OFF-` display UID scheme; use
   whatever identifier the existing system already has.
8. ~~QA forms~~ / 9. ~~QA outcome routing~~ — 💤 **parked** (QA module deferred).
10. **Status badge + Due-In colour rules** — ✅ **Decided: defined by the stage status simulator**
    (`stage status simulator.html`). **Stage** (7-dot track: PO Intake → Design → Material/Nesting
    → Production → QC → Dispatch → Delivered) tracks **position**. **Status** (green/amber/red/gray)
    tracks **health**, independently of stage:
    - **Green (On track):** default — no blockers, no timing risk.
    - **Amber (Held):** open design query, awaiting customer approval, or any non-time blocker.
    - **Red (Delayed):** `days_to_due < days_typically_needed_from_current_stage` — a timing rule,
      not a manual flag.
    - **Gray:** not started / delivered (terminal states).
    Stage and status move independently — a job can sit at "Design" while its status flips
    green→amber→green as queries open and close.
11. ~~**Canonical order record**~~ — ✅ **Closed: resolved by the fresh-rebuild decision.** The three
    overlapping old tables (`quotation.order`, `order_booking.order_booking`, `transactions.project`)
    are not carried over. The new chain is **`Quotation` → `SalesOrder` → `JobCard`**, one clean
    record per phase, so there is nothing to consolidate.

---

## 11. Project / App Structure *(decided)*

Modules are organized **by role/department**, matching who owns which screens:

```
shop_management/
├── inventory/       Django project package (settings, urls) — existing
├── core/            shared: status state-machine, numbering (QTN/SO/JC/GRN),
│                    notifications, audit log, base templates
├── masters/         ALL reference data: customers, material/thickness/finish/etching,
│                    PSI rates, station master, reorder levels
├── users/           User model, role flags, login, role-landing
├── sales/           Sales + Sales Manager: quotation form + items, PDF + auto-email,
│                    My Quotations (versioning, Upload PO, resend), Manager Approvals
├── accounts/        Accounts dept (decided name): confirm/reject PO, convert to SO,
│                    order booking dashboard, move to design
├── design/          design queue, job detail, drawing revisions, sheet reserve, queries
├── production/      ONE app, two view layers:
│                      views_manager.py — ready queue, priority 1–4, send to floor
│                      views_floor.py   — job cards, station selection, mark complete
├── stores/          sheets/offcuts, GRN inward, issue, reorder alerts
├── qa/              future phase (deferred)
│
├── templates/       COMMON templates folder (decided) — one subfolder per module:
│   ├── base.html        shared layout (topbar, nav, styles)
│   ├── masters/         customer_list.html, station_master.html, …
│   ├── users/           login.html
│   ├── sales/           quotation_form.html, my_quotations.html, approvals.html
│   ├── accounts/        order_booking_dashboard.html
│   ├── design/          design_queue.html, design_job.html
│   ├── production/      ready_queue.html, job_card.html
│   └── stores/          inventory.html, log_sheet.html
└── static/          shared CSS/JS/fonts
```

- **Templates are COMMON** (decided): one root `templates/` folder with a subfolder per module
  (`templates/masters/`, `templates/sales/`, …) — not per-app template folders. Matches the
  existing project layout.
- **Production Manager + Floor Manager share one app** — same models (JobCard, JobCardStation);
  separation is at the views/templates/permissions level, not the app level.
- **Fresh codebase, separate from the live system (decided):** the new project is built in its
  own folder/repo — the old system keeps running untouched for the shop's daily work. At
  **cut-over** (when the new system is fully built), masters + current inventory are imported
  (§6) and the shop switches over.

---

### Appendix — Mockup ↔ Existing model quick map

| Mockup screen | Primary existing model(s) | New model(s) needed |
|---------------|---------------------------|---------------------|
| Login | `users.User` | new role flags, role landing (no OTP) |
| Quotation + My Quotations (PO upload) | `quotation.order`, `order_child`, `PSI`, `sales_customer` | quote version, PO fields |
| Manager Approvals (no analytics) | `quotation.order.is_approved`/`is_declined`/`rejection_reason` | — (reuse) |
| Order Booking (Accounts confirm PO → SO) | `order_booking.order_booking` | PO-confirm, SO mint, DrawingRevision |
| Design Queue | `order_booking.order_booking`, drawings | DesignQuery |
| Design Job (manual sheet pick) | `store.product_qr`, `store.shelf`, `material_history` | Material reservation, DrawingRevision |
| Production Ready Queue | `order_booking.production_orders`, `project_working_order` | Ready-gate flag |
| Job Card Stations | `production_orders`, `store.cutter` | ProcessStation (master), JobCardStation |
| Stores | `store.product_qr`, `shelf`, `dealer`, stock tables | GRN, reorder_level |
| QA | *(none)* | Deferred — future phase (§7.2) |
