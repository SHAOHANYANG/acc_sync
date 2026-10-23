# Accelerometer Synchrony
Measuring movement synchrony between subjects wearing Bangle.js 2 smartwatches.

## Setup

Navigate to the project directory and run:
```bash
pip install -e .
```

There's some sample data in the `data/` directory, but you'll need to put the actual parquet files there.

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
