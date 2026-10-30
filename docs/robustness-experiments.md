# Window-origin robustness and synthetic stress experiments

These experiments extend the existing 2s/4s energy-duration pipeline. They do
not recompute STFT or introduce a new synchrony model. No clinical data are
used in the benchmark; simulated performance is not clinical accuracy.

## Origin sensitivity in the controlled pipeline

`run_real.py` now saves, per session, an `origin_sensitivity.csv`, an explanation,
and an `origin_sensitivity.html` figure. All real tables stay in `results/tables/`
and all real figures stay in `results/figures/`; the AI opens neither. The
existing numeric console whitelist is unchanged.

For every existing rule, evaluate bin origins `o + f*w`, where `o` is the
configured origin, `w` is 2 or 4 seconds, and `f` is 0, 0.25, 0.5 or 0.75.
Thresholds, requested durations and missing-data handling stay fixed. No
origin is selected as optimal. This is bin placement, not clock alignment.

Candidate time Jaccard is intersection duration divided by union duration,
restricted to time valid under both grids. An empty union is undefined, not
perfect agreement. Full-grid event counts can have different edge coverage.
Missing windows still split events. A 2-second duration becomes at least 4
seconds in the 4-second grid; the primary controls use 4/8 seconds so their
effective durations match across widths.

## Reproducible invented benchmark

```powershell
python run_fake_tests.py
python run_synthetic_benchmark.py
```

The benchmark accepts no study-input path. It generates only numbered files
under `fake_data/benchmark/run_.../` and writes only `fake_results/benchmark/run_.../`.
The accepted input structure matches the controlled CSV contract. Generated
replicates and outputs are ignored by Git.

Six prespecified scenarios, eight seeded trajectories each:

1. Mixed: a weak 20-second event, a strong 4-second event and a brief lower-energy
   nonsynchrony burst.
2. Weak energy near the lower threshold, with more frame noise.
3. A missing interval through a weak event, retained and explicitly accounted for.
4. Sustained nonsynchrony with exactly the same energy as scenario 1 for each seed.
5. A strong 2.4-second event, to expose duration and boundary limitations.
6. A constant/no-event control with small noise.

Labels are set by scenario before generating energy, never derived from the
tested thresholds. Scenario 4 is an identifiability counterexample: an energy-only
algorithm cannot tell two identical curves apart even if the true activity differs.
It is not evidence about how often this happens in real recordings.

Each trajectory lasts 96 seconds at 0.48-second frame steps. This is an energy-level
simulation; it does not model the 5.12-second STFT response. Random event offsets
and frame noise vary across seeds. The base seed is 20261029.

- 2 widths × 4 origins × 24 rules × 48 trajectories = **9,216 detection runs**.
- Twelve single rules: thresholds 2/4/6 × durations 2/4/8/12 seconds.
- Twelve dual rules: three threshold pairs × short durations 2/4 × long 8/12 seconds.
- Three prespecified controls: high/short (6,4s), lower/long (2,8s), or either branch.
- For these controls, IoU 0.25/0.5/0.75 yields **3,456 matching evaluations**.
- Independent-label energy comparison runs once per trajectory/width/origin.

Event matching reuses maximum-cardinality one-to-one IoU matching. Missing-crossing
references are reported as excluded, not as successes or misses. All negative time
is explicitly labelled in the invented scenarios, so their unmatched eligible
predictions can be called false positives. Recall/precision are undefined when
their denominator is zero. No accuracy, IID p-value or confidence interval is produced.

The same trajectories appear at multiple grid origins. Summed counts describe
repeated trajectory/grid evaluations, not independent people. The three controls
were fixed before seeing outcomes; no best clinical rule is selected from the scan.

## Generated deliverables

- `report.md`: full comparison table and limitations, in Chinese.
- `benchmark.html`: local interactive recall/false-positive/origin/IoU comparisons.
- `synthetic_findings.png` and `.pdf`: standalone four-panel meeting figure.
- `all_parameter_runs.csv`, `prespecified_rules.csv`,
  `independent_energy_comparison.csv`, `matching_sensitivity.csv`.
- `benchmark.json`: seeds, rules, coverage and matching parameters for reproduction.

## What the completed benchmark shows

In the mixed clean scenario, over eight trajectories × four origins (64 reference
event evaluations), the high/short control gets 26 hits with 2s bins and 20 with
4s bins. The dual control gets 58 and 62 respectively. In the paired nonsynchrony
scenario, the same dual outputs become 58 and 64 false positives. These are
constructed stress-case counts, not estimates of real sensitivity or specificity.

Near-threshold noise and arbitrary bin placement change the outcomes. A brief
2.4-second event is missed by the three prespecified controls with a minimum
4-second high-energy branch; increasing the output-bin width does not restore
the event's original duration. Changing the matching IoU also changes reported
Hit/Miss, without changing the detections themselves.

The next real-data step remains `run_real.py --check`, then `run_real.py` if valid.
Independent labels, their clock and completeness still require genuine confirmation.
Do not enable them just to obtain metrics. Emily's actual segmentation is still
needed to evaluate her method. No benchmark result resolves that dependency.
