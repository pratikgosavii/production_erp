# Demo Logins — Ravi Raj Anodisers ERP

**Every account uses the same password: `demo1234`**

These accounts are created by `python manage.py seed_demo --reset`. Re-running that
command wipes and rebuilds them, so any password you change by hand is lost on the
next reseed.

Login page: `/users/login/`
Locally: http://localhost:8000/users/login/

You land on your own screen automatically — the role tabs on the login form are a
visual hint only and grant nothing. Access is enforced server-side, so typing another
department's URL gives a 403 rather than a peek.

---

## Department roles

| Role | Username | Name | Lands on |
|---|---|---|---|
| Sales | `arjun` | Arjun Patil | My Quotations |
| Sales | `imran` | Imran Sheikh | My Quotations |
| Sales | `sneha` | Sneha Rao | My Quotations |
| Sales Manager | `vikram` | Vikram Mehta | Approval Queue |
| Accounts | `priya` | Priya Nair | Order Booking |
| Design | `meera` | Meera Deshpande | Design Queue |
| Production Manager | `sunil` | Sunil Rao | Production — Sales Orders |
| Production Floor Manager | `ramesh` | Ramesh Tawde | Job Cards |
| Stores | `deepak` | Deepak Salvi | Material Inventory |
| QA | `anita` | Anita Kale | Stores (QA module deferred) |

## Admin

| Username | Purpose |
|---|---|
| `admin` | Superuser — Django admin at `/admin/`, plus the Item Code and Customer masters |

## Shop-floor operators

These five are pickable as station operators on a job card. They hold no department
role, so they have no screens of their own — they exist to be named against work.

`ganesh` · `farida` · `ravik` · `sudha` · `imrank`

---

## Who can open what

| Screen | Roles |
|---|---|
| My Quotations, Quotation Form, Design Queries | Sales |
| Quotation Full Detail + PDF | Sales (own only), Sales Manager, Accounts, Admin |
| Approval Queue, All Quotations | Sales Manager |
| Customer Master | Sales, Sales Manager, Admin |
| Item Code Master (approved rates) | Admin only |
| Order Booking, Sales Order Detail | Accounts (+ Sales Mgr, Prod Mgr on the SO detail) |
| Design Queue, Design Job Detail | Design |
| Material Inventory | Stores (full), Design & Production Manager (read-only) |
| Production Sales Orders, Priority Board | Production Manager |
| Job Card | Floor Manager (runs it), Production Manager (read-only) |

Two rules worth knowing when demoing:

- **A salesperson only sees their own quotations.** `arjun` opening one of `imran`'s
  gets a 403. Managers, Accounts and Admin see everything.
- **Read access is not write access.** Design can browse stock but cannot issue it;
  the Production Manager can open a job card but cannot start work on it. Both are
  enforced in the view, not by hiding the button.

---

## Demo walkthrough

The full chain, one login per step:

1. `arjun` — raise a quotation, submit for approval
2. `vikram` — approve it (this also locks the item-code rate bands into the customer's rate card)
3. `arjun` — download the quotation PDF, send it yourself, then upload the customer's PO
4. `priya` — confirm the PO; the Sales Order number is minted here
5. `meera` — upload a drawing per item code, reserve a sheet, approve & send to production
6. `deepak` — mark the reserved material **Issued** (until this, the item stays invisible to production)
7. `sunil` — set priority, select ready items, create a Job Card
8. `ramesh` — Start Work, tick the applicable stations, mark each complete
9. `deepak` — mark the order dispatched

---

## Before this goes near real data

- These are shared demo credentials with a published password. Delete or reset them.
- `DEBUG = True` and `ALLOWED_HOSTS = ["*"]` are still set in `production_erp/settings.py`.
- `SECRET_KEY` is committed in that same file, which means anyone with the repo can
  forge session cookies.
