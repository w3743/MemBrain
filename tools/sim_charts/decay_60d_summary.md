# 60-day memory decay simulation

Initial strength: 0.6; initial decay rate: 0.02.
Dots in the curve chart indicate successful uses/retrievals.

| Frequency | Uses | Day 7 | Day 14 | Day 30 | Day 45 | Day 60 | Final decay |
|---|---:|---:|---:|---:|---:|---:|---:|
| Never (0x) | 0 | 0.477 | 0.372 | 0.200 | 0.107 | 0.056 | 0.02000 |
| Once (1x) | 1 | 0.637 | 0.540 | 0.355 | 0.231 | 0.146 | 0.01498 |
| Every 30d (2x) | 2 | 0.637 | 0.540 | 0.355 | 0.457 | 0.351 | 0.00984 |
| Every 14d (5x) | 5 | 0.637 | 0.540 | 0.697 | 0.723 | 0.755 | 0.00506 |
| Weekly (9x) | 9 | 0.637 | 0.683 | 0.837 | 0.875 | 0.900 | 0.00402 |
| Every 3d (20x) | 20 | 0.841 | 0.892 | 0.945 | 0.965 | 0.974 | 0.00269 |
| Daily (60x) | 60 | 0.962 | 0.988 | 0.995 | 0.997 | 0.998 | 0.00100 |
