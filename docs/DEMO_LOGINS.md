# Demo Guide — Ravi Raj Anodisers ERP

**Every account uses the same password: `demo1234`**

Login page: http://localhost:8000/users/login/
Start the server with `python manage.py runserver`.

Every record named in this guide exists in the demo database right now. If you
reseed (`python manage.py seed_demo --reset`) the numbers change — re-read them
off the screen rather than this page.

---

## 1. Logins

| Role | Username | Name | Lands on |
|---|---|---|---|
| Sales | `arjun` | Arjun Patil | My Quotations |
| Sales | `imran` | Imran Sheikh | My Quotations |
| Sales | `sneha` | Sneha Rao | My Quotations |
| Sales Manager | `vikram` | Vikram Mehta | Approval Queue |
| Accounts | `priya` | Priya Nair | Order Booking |
| Design | `meera` | Meera Deshpande | Design Queue |
| Production Manager | `sunil` | Sunil Rao | Production — Sales Orders |
| Floor Manager | `ramesh` | Ramesh Tawde | Job Card Queue |
| Stores | `deepak` | Deepak Salvi | Material Inventory |
| QA | `anita` | Anita Kale | NCR Register |
| Admin | `admin` | — | Django admin |

Operators (`ganesh`, `farida`, `ravik`, `sudha`, `imrank`) exist for the station
roster but have no screens of their own yet.

**The role chip on the login form is checked.** Sign in as `arjun` with the
**Stores** chip selected and you are refused, told *"this account is Sales"*, and
the chip moves itself to Sales. Pick the right chip and you are in. Access itself
is enforced server-side regardless — typing another department's URL gives a
**No access** page, not a peek.

---

## 2. The full walkthrough

Nine steps, start to finish. Each one is a different login, which is the point —
no single person sees the whole chain.

### Step 1 — Sales raises a quotation
**`arjun`** → **+ New Quotation**

Pick **Veeransh Fabricators**, then type item code `VF-DP-080`.

Watch what happens: the code is recognised, and its **approved price ladder fills
itself in** — `1–100 @ ₹175 · 101–200 @ ₹111 · 201–500 @ ₹95`. Nobody retypes a
rate that was approved once.

Now change the middle band's **to** quantity from `200` to `300` and the row turns
amber: *"101–300 overlaps the approved bands 101–200, 201–500"*. **Submit greys
out** — the server would refuse it, so the form refuses first. Put it back to
`200` and Submit returns.

### Step 2 — Sales Manager approves
**`vikram`** → Approval Queue

`QTN-0422` (Marigold Textiles) has been waiting **84 days** — the queue sorts
oldest-first because age is the thing that matters.

Open it. Each item shows **area in sq.inch and rate per sq.inch**, which is the
only way to compare a 80×50 plate against a 200×150 one. Approve it, and every
slab on it is written to that customer's rate card — not just the one matching
this order's quantity.

### Step 3 — Sales records the customer's PO
**`arjun`** → find `QTN-0488` (Awaiting PO) → **+ Upload PO / Drawing**

Once the PO is on, the row action reads **Send to Accounts →**, not "Convert to
Sales Order". Sales hands over; Accounts mints the SO number.

Try `QTN-0490` — PO already uploaded, waiting to be sent.

### Step 4 — Accounts books the order
**`priya`** → Order Booking

Book the quotation into a Sales Order. From here the **item code**, not the order,
is the tracked unit. Open any SO (e.g. `SO-1031`) to see the per-item table:
rate, drawing, **sheet reserved**, status, job card, package QR.

### Step 5 — Design draws it, then the customer signs it off
**`meera`** → Design Queue

Open `SO-1069` / `BS-RAT-100` — no drawing yet. Upload one and it becomes
**Rev 01**.

Note the **Customer drawing** block, separate and dashed: that is what the client
sent in with the PO. It never gets superseded and it never counts as "drawn" —
it is the brief, not the answer.

Now the new gate. Press **Record Proof Sent**. Three items are already mid-flight
so you can see all three states without waiting:

| Order | Item | State |
|---|---|---|
| `SO-1031` | MT-LBL-060 | **Proof Rev 01 sent** — waiting on the customer, 4 days |
| `SO-1032` | VF-NP-150 | **Changes requested** — *"Logo sits 2mm low…"* |
| `SO-1033` | VF-DP-080 | **Rev 02 approved** — free to move |

An item waiting on a proof is blocked as *"Held — awaiting customer proof
approval"*. The blocker is named as the customer, not your team.

### Step 6 — Sales closes the proof round
**`arjun`** → Design Queries

The **Artwork proofs awaiting the customer** inbox sits at the top. Only the
person who spoke to the customer can close a round, so it lives here, not in
Design. Press **Approved**, or **Changes** — changes without the customer's actual
words is refused, because Design cannot act on *"they want changes"*.

### Step 7 — Design reserves material, Stores issues it
**`meera`** → same job → **Raw Material**

Offcuts and leftovers sort to the top, so remnants get used before anyone cuts a
fresh 6×3 sheet. Each row shows what it is (`SHEET` / `OFFCUT`) *and* where it is
in life (`Available` / `Leftover`), plus the parent it came from.

While a sheet is only reserved it can still be swapped. Once Stores issues it,
Design is locked out — the plate is on the floor.

**`deepak`** → Material Inventory → find `SHT-0144` (Reserved) → **Issue**

It confirms first, showing exactly where the plate is going: sheet, rack, SO,
customer, item code, quantity and who reserved it. No undo on this screen, so it
asks before the plate leaves the rack.

### Step 8 — Production Manager plans the work
**`sunil`** → Production — Sales Orders

He can card an item **before** Stores has issued it — planning does not wait on a
trolley. An unissued item shows an amber *"awaiting material"* caveat but stays
selectable.

Tick item codes → **Create Job Card from Selection**. A panel is one material at a
time, so a card mixing **SS 304** with **Aluminium** is refused outright, naming
both specs.

Then the stage instructions appear — a box per station. Write what the operator
needs to know *for this job*, e.g. at Chemical Etching:

> SS304 2mm — bath at 42°C, 28 Baumé. Etch 14 min, check depth at 7.

Those belong to this card. The next job in a different metal gets its own.
**Skip & create without instructions** is there for routine work.

Afterwards use **✎ Add / Edit Instructions** on the card — it reports coverage
("Instructions written for 4 of 12 stages") and locks any station that has
already run, because rewriting what an operator was told *after* he acted on it
is exactly what an audit looks for.

Also try the **Priority Board** — drag a card between P1–P4 columns.

### Step 9 — The floor runs it
**`ramesh`** → Job Card Queue

`JC-0518` is mid-run, 8 of 10 stations done. Open it.

Each station shows its **Min/Max band**. Most are universal; **Chemical Etching**
and **Colour Filling** show a material-specific band in amber, because those two
genuinely vary with the metal — `SS 316 · 3.0mm` runs 38–64s where
`Aluminium · 0.5mm` runs 7–11s.

Press **▶ Start** on the current station. The clock runs from that press, not from
when the last station finished — overnight and trolley waits are no longer counted
as run time. The state chip goes green → amber at 80% → red past max.

Press **✓ Complete** and the production log opens: **Qty In / Qty Out / Qty
Rejected**, with reason and disposition appearing only if something was rejected.
Qty In pre-fills from the previous station's good output, so you type one number.
It refuses to close if the figures don't balance.

The right rail keeps a live count: **Started · Good · Rejected · Yield %**.

Try rejecting **6 or more** pieces. An **NCR is raised automatically** off the data
you just logged — no second form.

A card that Stores has not issued for cannot start at all: `JC-0519` shows
**Start Work** disabled with *"Stores owes: …"*.

### Step 10 — Quality closes the loop
**`anita`** → NCR Register

Four non-conformances, one from each door the spec names:

| | Source | State |
|---|---|---|
| `NCR-0002` | Production rejection | Open — nobody has looked |
| `NCR-0003` | QC inspection failure | Investigating, **overdue** |
| `NCR-0004` | Customer complaint | Closed, full CAPA |
| `NCR-0005` | Supplier non-conformance | Open |

Try closing `NCR-0002` with the boxes empty — refused: *"cannot be closed without
a root cause and a corrective action"*. Fill both and it closes. `NCR-0004` shows
what a finished one reads like.

---

## 3. Worth showing if there is time

- **Job card edit/delete** (`sunil`, on the order screen) — full edit before Start
  Work; after it, delete only, and the dialog states what recorded work is
  destroyed. The audit entry is written *before* the delete, since the stations
  cascade away with the card.
- **Offcut logging** (`deepak`, Sheet Detail → ✂ Log Offcut) — the only thing in
  the system that creates an `OFF-` sheet, linked to the parent it was cut from.
- **Access control** — sign in as `meera` and open
  `/production/dashboard/`. A branded **No access** page naming your actual role,
  not a stack trace.
- **Rate card auto-approval** (`arjun`) — quote `201–301 @ ₹95` against an approved
  `201–500 @ ₹95` and it needs no approval at all; the rate card does not change.
  Quote `201–301 @ ₹90` and it goes to the manager, whose approval splits the band
  into `201–301 @ ₹90` + `302–500 @ ₹95`.

---

## 4. Known gaps — say these before someone finds them

- **Only chemical etching is modelled.** Chemical milling and laser cutting are in
  the spec and not built.
- **Station times are placeholders in seconds**, not a real time study. The
  structure is right; the numbers are invented.
- **Stage instructions are blank on older cards** — they were added recently.
- **Three job cards mix materials** (`JC-0512`, `JC-0513`, `JC-0518`). They pre-date
  the one-material rule and carry a warning banner.
- **Not built at all:** packing and dispatch (gate scan, outward register), panel
  sub-UIDs, PO match review, invoicing, machine maintenance, operator skill
  matrix, reporting/KPI, and the rest of the quality suite (calibration, internal
  audit, approved vendor list, document control).

---

## 5. Before this touches real data

Not production-ready as it stands:

- `DEBUG = True` in `production_erp/settings.py`
- `ALLOWED_HOSTS = ["*"]`
- `SECRET_KEY` is committed to the repository
- Every account shares the password `demo1234`
- Uploaded files are served by Django itself

Fix all five before any real customer data goes in.
