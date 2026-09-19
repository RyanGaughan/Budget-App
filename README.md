# Budget App — multi-user web app

Flask web app: login, upload credit card/bank statement PDFs, get an
auto-categorized spending dashboard with recurring-charge detection. Built
for a household — each person has their own separate login, with an
optional combined view of everyone's spending together.

## Deploying (Render)

1. **Push this code to GitHub** (from a computer):
   ```
   git remote add origin <your repo URL>
   git push -u origin main
   ```
2. **On Render.com**: New → Blueprint → connect your GitHub repo. Render reads
   `render.yaml` in this folder and provisions both the Postgres database and
   the web service automatically — no manual config needed.
3. Wait for the first deploy to finish (a few minutes).
4. **Seed accounts**: in the Render dashboard, open the web service → Shell tab, run:
   ```
   python3 seed_users.py --household "Your Household Name" ryan ellie
   ```
   This prints a temporary password for each username. Share each one with
   its person (they'll be forced to set their own password on first login).
   Leave off `--household "..."` for accounts that should NOT be able to see
   each other's data.

That's it — the live URL is shown on the Render service's page.

## Adding more people later

Re-run `seed_users.py` with the new username(s) (same `--household` name to
add them to the same combined view, or without it to keep them fully
separate). Existing accounts are left untouched.

## What's in here

- `app/` — the Flask application (models, auth, routes, templates)
- `app/parsers/` — PDF parsing, categorization, recurring-charge detection
  (same logic validated against real statements)
- `wsgi.py` — app entry point (gunicorn runs `wsgi:app`)
- `seed_users.py` — creates/links user accounts
- `render.yaml` — Render Blueprint (web service + Postgres, wired together)
- `requirements.txt` — pinned dependencies

## How the household view works

Every upload is attributed to the person who uploaded it. Each user's own
dashboard only shows their own transactions. If two or more users share a
`household_id` (set via `seed_users.py --household`), each of them additionally
gets a "Combined household" toggle showing everyone's spending together, with
a per-person breakdown. Nobody outside that household can see it.

## Known limitations (same as the standalone script version)

- **PDF parsing is regex-based.** Tested against real Citi credit card
  statements (which use a "MM/DD MM/DD DESCRIPTION CITY STATE AMOUNT" format
  with a 2-column layout) plus synthetic statements. Other issuers' formats
  may need small tweaks — if a statement extracts 0 transactions or looks
  wrong, send it over and the parser can be adjusted.
- **Categorization is keyword-based**, not ML — transparent and free, but
  needs a few keyword additions per person's actual merchants. The
  "Uncategorized" page in the app shows exactly which merchants need a rule,
  ranked by how often they show up.
- **Recurring detection** needs at least 2 occurrences on a fairly regular
  cadence (weekly/monthly/quarterly/annual) with consistent amounts (~15%
  tolerance) to be flagged. A charge on its 2nd occurrence is the earliest it
  can be detected.
- Uploaded PDFs are parsed in memory and not stored — only the extracted
  transaction rows are saved to the database.

## Next ideas

- Per-category monthly budgets with over/under indicators
- Month-over-month trend view (not just totals)
- A ranked "cut this, save $X/year" list combining recurring + top-merchant data
- Support more statement formats as they come up
