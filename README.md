# Accelerometer Synchrony
Measuring movement synchrony between subjects wearing Bangle.js 2 smartwatches.

## Required research workflow (2026-10-29)

Read [AGENTS.md](AGENTS.md). Research inputs may be processed **only** through
`run_real.py`, after `python run_fake_tests.py` passes. The AI sees only closed-schema
numeric aggregates, rounded to three significant digits, or sanitized errors.
It never opens research figures or detailed tables.

```powershell
python generate_fake_data.py
python run_fake_tests.py
python run_real.py --fake
python run_real.py --check
python run_real.py
```

See [the controlled-entry guide](docs/private-runner.md) for the input contract,
output whitelist, annotation requirements, and tested boundaries. Data and
results are never committed. Legacy API/CLI examples below are reference for
synthetic development only; earlier direct real-data commands are superseded.

## Setup

Navigate to the project directory and run:
```bash
pip install -e .
```

Research inputs are not tracked. The controlled workflow accepts numbered
numerical energy CSVs and timestamp-only intervals in `data/`, not raw Parquet.

## Usage

```python
import acc_sync as acs

session = acs.util.load_session_data(
    pt="RF001",
    session="2025_11_15",
    roles=["client", "therapist"],
    hands=["R", "L"]
)

acc_mag = acs.util.prep_session_mag(
    session, start_time='15:03:00', end_time='15:32:00'
)

for wearable in session:
    viz.plot_acc(session[wearable]).update_layout(
        title=f"Accelerometer Data: {wearable}"
    ).show()

viz.plot_mag_dict(acc_mag).show()
```

## Offline window segmentation and event evaluation

The `sync_eval` module reuses an existing energy curve. It adds configurable
video-time alignment, non-overlapping 2 s / 4 s means, exploratory low/medium/high
segments, and one-to-one event-level Hit/Miss evaluation. It does not change the
original signal-processing code.

No video is required for initial segmentation: prepare with `--time-origin first`
to use curve-relative seconds. The output is marked as not video-aligned;
video-based annotation evaluation requires a confirmed shared time reference.

Start with entirely synthetic inputs (Python 3.10+; no extra dependencies for the core):

```bash
python -m unittest discover -s tests -v
python -m sync_eval demo
```

See [the offline usage guide](docs/offline-evaluation.md) and the
[annotation template](templates/session_01_annotations.csv). Real-data commands
are run only by the researcher in the approved local environment. Numbered
exports, mapping tables and derived results remain in ignored local folders;
only code, blank templates and explicitly synthetic examples belong in Git.
The current entry for all research processing is `run_real.py`; the older guide
is retained only as analysis-method documentation.

## Energy + Duration experiments

`sync_eval explore` reuses those window means and compares two interpretable
rules: a single energy threshold with a minimum continuous duration, and a
higher-energy/shorter-duration OR lower-energy/longer-duration rule. It records
every parameter, event interval, and 2 s / 4 s comparison without selecting an
unvalidated "best" rule. Optional independent positive AND negative interval
labels support descriptive energy-group comparison and point-biserial r;
energy-derived labels are not accepted as validation references.

```powershell
python -m sync_eval explore-demo --output-dir local-results/synthetic-energy-duration-demo --plot
```

This command creates invented data only. See
[the experiment guide](docs/window-energy-experiments.md) for exact rules,
parameter grids, label requirements, and researcher-only real-data commands.
