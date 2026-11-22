# 18 · Daily emergency briefing

## Problem
Every day the coordinator needs a one-page briefing. Field teams report numbers by message, but those numbers do not always agree with the call log, warehouse stock and distribution records. A briefing that mixes them silently is unsafe.

## Intended user
Emergency coordinators.

## Technique
Straightforward calculation and rule-based comparison (no model). Each figure is a count or sum over the records of the chosen day: calls (and the repeat calls among them), complaints opened, service requests created, distinct households and parcels and meals distributed, parcels planned, and stock on hand from `v_stock_on_hand`. Parcels, ready-to-eat meals and hygiene kits are separate figures in separate units and are never added together. **Conflict rule:** a field update with `figure_key = households_reached` and a site is compared with the number of distinct households in `distributions` for that site and day; any difference is a conflict.

## Inputs
Tables `field_updates`, `calls`, `complaints`, `service_requests`, `v_stock_on_hand`, `distributions`, `distribution_plans` (read) and `briefings`, `briefing_sources`, `audit_log` (written). Each figure is stored in `briefing_sources` with its source table and record IDs, and shown with them.

## Assumptions
- A day is the calendar date of the record's timestamp.
- Stock on hand is the current balance, not the balance at the end of the briefing day.
- Only `households_reached` is compared; other field figures (for example `household_visits`) have no matching record and are listed as text only.
- Only coordinators (or admin) review, edit and approve in local demo mode (sidebar selector).

## Limitations
- One comparison rule and exact equality, with no tolerance.
- Conflict decisions: "records are right" (resolved), "field report is right, records need correcting" and "cannot decide" (both listed as unresolved items in the export).
- Text edits change the Markdown body only; figures and sources stay as calculated.
- One page: the draft body is the figures table (11 rows, source IDs cut to six per figure, the rest in the appendix of the export), at most the five newest field updates (each cut to 160 characters, then a line "and N more in the source records") and a short notes line, about 30 lines whatever the day. Every figure stays linked to all its records in `briefing_sources` and the appendix. The page's figures table shows the first two record IDs and "(+N more)"; a selector below it shows all IDs of one figure. A test builds a day with 60 field updates and checks the body stays within 50 lines and 4000 characters.
- No extra libraries beyond `requirements.txt`.

## Launch
```
python run.py 18
streamlit run projects/18-daily-emergency-briefing/app.py --server.port 8518
```
Port 8518.

## Screenshot
![Daily emergency briefing page](screenshot.png)

## Try this demo
The page opens on the seeded conflict date (`demo.scenarios` -> `p18`, key `conflict_date`: the day before the seed day, so it moves with the seed). Counts of calls, complaints and service requests are random per seed and differ from day to day, so they are not quoted here; the distribution, field-update and stock figures below are fixed by the seed content and are the same whatever day you seed.
1. Press "Generate draft briefing". A draft (the next free ID after `BRF-0001`) is created for the date shown, with 11 figures. Under "2. Figures and their source records" every figure has a unit, a source table and record IDs. Check that "Calls received" equals the number of call IDs listed beside it, and that "Households reached" (37) shows 37 `DST-` IDs. Parcels (55) and meals (378) are separate rows with separate units.
2. Section 3 shows one conflict: `FU-0008` at `SITE-02` says 52 households, the records (37 `distributions` rows) give 37, difference +15. Status: NOT REVIEWED. Section 5 says approval is blocked until it is reviewed, and offers no approve button yet.
3. Choose a decision, add a note, press "Save review". The conflict now shows the decision and the badge changes; section 5 now offers the approval form.
4. Optional: in section 4 change the Markdown text and press "Save edits" ("Edits saved."). The change is in the exported file later.
5. In section 5, press "Approve briefing" without ticking the box: "Tick the confirmation box first. Nothing was changed." Then tick the confirmation box, choose a coordinator and press "Approve briefing". The page shows "Approved by ... at ..." and the text can no longer be edited.
6. Press "Download approved briefing (Markdown)" (or open "Preview export"). The file holds the figures table, your edited text, the conflict review with both numbers (52 and 37), an "Unresolved items" section (empty after "records are right"; the conflict listed after "cannot decide" or "field report is right") and an appendix with every record ID. Each figure in the file equals the count of the IDs the appendix lists for it. The audit log (Project 20) has create, resolve, update (if you edited) and approve entries for the briefing.

## Tests
`tests/test_p18_briefing.py`: figures equal independent SQL on the records; parcels and meals stay separate units; every figure has a source table and IDs; the seeded conflict (52 vs 37) is flagged and a matching figure is not; approval refused without confirmation, for an unknown or non-coordinator ID, and while a conflict is unreviewed; the export contains the approved figures, conflict review, unresolved items and appendix; approved briefings are locked; audit trail; the draft body is short enough for one page and lists parcels and meals as separate rows; the coordinator edits the text in the page before approval and the edit reaches the export; the app starts (`app_test(18)`).
