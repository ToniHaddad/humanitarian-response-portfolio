# Group B progress (data intake and quality)

Director: Group B. Database copy: `data/directorB.db`.

| # | Folder | Built | Tests pass | Walkthrough checked | Screenshot | Notes |
|---|--------|-------|------------|---------------------|------------|-------|
| 05 | 05-emergency-data-entry | in progress | - | - | - | agent briefed |
| 06 | 06-family-record-checker | in progress | - | - | - | agent briefed |
| 07 | 07-field-data-pipeline | in progress | - | - | - | builds on `shared/pipeline.py` |
| 08 | 08-offline-field-sync | in progress | - | - | - | builds on `shared/sync.py` |

## Shared modules owned by Group B
- `shared/pipeline.py`: `run_pipeline(conn, *, trigger, ...)` entry point for Project 07 and the Project 20 scheduler.
- `shared/sync.py`: server side of offline sync (apply a queued change, detect conflicts, resolve only with a staff choice) for Project 08 and the Project 20 API.

## Not verified
- (none yet)

## Next step
Review the four agents' work as it arrives.
