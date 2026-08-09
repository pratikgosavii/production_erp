# START HERE — context handoff for a new session

This project (`production_erp`) is a **fresh rebuild** of the Chemical Etching Works ERP.
All planning was done in an earlier session; the conclusions live in **`docs/PROJECT_DESIGN.md`**.

## First thing to do in a new chat

> Read `docs/PROJECT_DESIGN.md` — that is the complete, agreed design. Then start Phase 1.

## The 60-second summary

**Flow:** Login → Sales creates Quotation (QTN) → Sales Manager approves/rejects (comment only) →
Sales sends to customer (auto-email PDF) → Sales uploads PO → **Accounts confirm PO → convert to
Sales Order (SO minted) → move to Design** → Design (drawing + manually pick & reserve a sheet from
Stores; can raise a query to the salesperson who made the quote) → **Production Manager** sets
priority 1–4 and sends to floor → **Floor Manager** opens Job Card, picks applicable stations,
marks each complete (time auto-computed) → Completed → Dispatch.

**Key decisions**
- **Fresh rebuild.** Old `shop_management` app stays LIVE and untouched; we build new and import
  masters + inventory at cut-over.
- **1 quotation = 1 SO**, and a quotation may hold **multiple line items**. Each item gets its own
  design job, job card (`JC-0001`) and (future) QA record.
- **8 roles:** Sales, Sales Manager, Design, Production Manager, Floor Manager, Stores, Accounts, QA.
- **Priority = 1,2,3,4.** **No OTP** (password login only).
- **Accounts can reject a PO** with a reason → salesperson re-uploads and resends.
- **Dropped:** margin/cost/benchmark analytics dashboard, nesting fit/yield calc, auto-offcut,
  dashboard KPI tiles.
- **Deferred:** QA module, invoicing/billing.

**Structure (see §11):** `core`, `masters`, `users`, `sales`, `accounts`, `design`, `production`
(one app, `views_manager.py` + `views_floor.py`), `stores`, `qa` (later) — with a **common root
`templates/`** folder containing a subfolder per module.

**Build order (see §8):** Foundation → Quotation+Approval → PO/Order Booking → Design →
Production → Stores → (QA later) → Cut-over import.

## Still open
See **§10.2** of the design doc for the remaining open questions (defaults are proposed for each).
