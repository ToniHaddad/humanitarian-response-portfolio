# Group B progress (data intake and quality)

Director: Group B. Database copy: `data/directorB.db`. Round 2 review done.

| # | Folder | Built | Tests pass | Walkthrough checked | Screenshot | Notes |
|---|--------|-------|------------|---------------------|------------|-------|
| 05 | 05-emergency-data-entry | yes | yes (48) | yes: DRF-0007/INT-0037, DRF-0008/INT-0038 as README states | retaken, real capture | no change needed except screenshot |
| 06 | 06-family-record-checker | yes | yes (27) | yes: MRG-0003 merge, counts corrected (5 records moved) | retaken | README steps rewritten to use IDs and `demo.scenarios`; one test no longer depends on same-name pairs |
| 07 | 07-field-data-pipeline | yes | yes | yes: 126 after run 1, 0 loaded and 37 duplicates on run 2, broken file fails | retaken after a run | `shared.pipeline.run_pipeline(conn, *, trigger, files=None, source_dir=None, skip_seen=None, actor_id)` stable; Project 20 scheduler calls it with `trigger=` only and reads `.status` / `.detail` |
| 08 | 08-offline-field-sync | yes (status set to built) | yes (21) | yes: edit offline + server edit -> CNF-0002, resolved with confirmation | retaken with open conflict | seed fix: INT-0015 now has 2 versions so CNF-0001 is base 1 / server 2; new test guards it |

## Shared modules owned by Group B
- `shared/pipeline.py`: `run_pipeline` for Project 07 and the Project 20 scheduler.
- `shared/sync.py`: server side of offline sync for Project 08 and the Project 20 API.
- Shared edit: `shared/seed.py`, one line (`n_versions` for INT-0015).

## Not verified
- Scheduled run in Project 20 not executed by me (only the signature was checked).
- Screenshots show names from the current seed; they will show old names if names change later.

## Next step
None for Group B.
