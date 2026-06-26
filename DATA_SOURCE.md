# dataset.csv — Training & Evaluation Data

## Source
This is the dataset the MediTrack ML module (5-model weighted ensemble) is
trained and evaluated on. The file is a 100,000-row anonymised personal-health
dataset containing 14 input features and 1 binary target.

## File details
- Filename: `dataset.csv`
- Rows: 100,000
- Columns: 16 (id, 14 features, target)
- Class balance: 75,179 negative (75.2%) / 24,821 positive (24.8%)

## Schema
| Column           | Type    | Description                                  |
|------------------|---------|----------------------------------------------|
| id               | int     | Sequential row id                            |
| age              | int     | Age in years (20-80)                         |
| gender           | string  | "Male" / "Female"                            |
| bmi              | float   | Body Mass Index (16-45)                      |
| daily_steps      | int     | Average daily step count (1000-20000)        |
| sleep_hours      | float   | Average sleep hours (4-10)                   |
| water_intake_l   | float   | Daily water intake in litres (0.5-5)         |
| calories_consumed| int     | Average daily calorie intake (1200-3500)     |
| smoker           | int     | 0 = non-smoker, 1 = smoker                   |
| alcohol          | int     | 0 = no, 1 = yes                              |
| resting_hr       | int     | Resting heart rate (50-110)                  |
| systolic_bp      | int     | Systolic blood pressure (90-190)             |
| diastolic_bp     | int     | Diastolic blood pressure (60-120)            |
| cholesterol      | int     | Total cholesterol mg/dL (130-330)            |
| family_history   | int     | 0 = no family history of disease, 1 = yes    |
| disease_risk     | int     | **TARGET** — 0 = low risk, 1 = elevated risk |

## Important — honest data-quality finding
An empirical correlation analysis (see dissertation §5.6.4) revealed that all
14 input features have Pearson correlation magnitude **below 0.01** with the
`disease_risk` target on the full 100,000-row dataset, and the per-class
feature means are within ±0.2% of each other. The supplied features are
therefore essentially **non-discriminable** on the supplied target, and the
5 trained models converge at the majority-class baseline (74-75% accuracy,
ROC-AUC ≈ 0.50). The dissertation reports this honestly rather than inflating
the numbers. The contribution of MediTrack is the **complete ML pipeline**
(ingestion, training, persistence, weighted-ensemble inference, threshold
mapping, UI integration, live evaluation), not the headline accuracy.

## How the file is loaded
The file is loaded by `ml_model.train_quick_model()` (lines 83-98) using
the `DATASET_PATH` environment variable if set, otherwise it looks in the
project root for `dataset (1).csv` then `dataset.csv`, then the parent
directory. If none is found, the system falls back to a synthetic 10k-row
dataset (and prints a WARNING) — this fallback is **not** used in the
results reported in the dissertation.
