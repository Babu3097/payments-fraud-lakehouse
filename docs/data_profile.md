# PaySim data profile

Profiled on 2026-10-04 by streaming the real file once. The same numbers are re-checked inside
Databricks after the bronze load (the first reconciliation, Phase 2).

## Source and licence

- **Dataset:** Synthetic Financial Datasets For Fraud Detection (PaySim) by Edgar Lopez-Rojas,
  <https://www.kaggle.com/datasets/ealaxi/paysim1>
- **Licence:** CC BY-SA 4.0. Credit the authors and keep the data out of this repository.
- **Cite:** E. A. Lopez-Rojas, A. Elmir and S. Axelsson, "PaySim: A financial mobile money
  simulator for fraud detection", 28th European Modeling and Simulation Symposium (EMSS),
  Larnaca, Cyprus, 2016.
- **File:** `PS_20174392719_1491204439457_log.csv`, 493,534,783 bytes, in a 186,385,561 byte zip
  (SHA-256 of the zip: `f7eef9ffad5cfa64a034143a5c9b30491d189420b273d5ad5723ca40b596613d`).

## Structure

| Column | Meaning | Observed |
|---|---|---|
| `step` | Hour counter | 1 to 743 (31 days, the last with 23 hours) |
| `type` | Transaction type | `CASH_OUT` 2,237,500; `PAYMENT` 2,151,495; `CASH_IN` 1,399,284; `TRANSFER` 532,909; `DEBIT` 41,432 |
| `amount` | Local currency | 0 to 92,445,516.64; no negatives |
| `nameOrig` | Originating customer | Always `C...` |
| `oldbalanceOrg`, `newbalanceOrig` | Origin balance before and after | |
| `nameDest` | Recipient | `C...` (4,211,125 rows) or `M...` merchant (2,151,495 rows) |
| `oldbalanceDest`, `newbalanceDest` | Recipient balance before and after | Always 0 for merchants |
| `isFraud` | Ground-truth fraud label | 8,213 rows (0.129%), only `TRANSFER` and `CASH_OUT` |
| `isFlaggedFraud` | The simulated system's rule flag | 16 rows, all `TRANSFER`, all also fraud |

Totals: **6,362,620 rows**, 11 columns, 0 malformed rows, 0 blank values, 0 unparsable numbers,
0 exact duplicate rows. Distinct originators 6,353,307; distinct customer IDs overall
(originator or recipient) 6,923,499; distinct merchants 2,150,401.

## Findings and what we do about them

| # | Finding | What we do |
|---|---|---|
| 1 | **The data is perfectly clean.** | Our quality rules would never fire, so the generator injects documented defects (duplicates, nulls, bad types, late files) with a fixed seed. PaySim itself must pass the expectations, which shows they do not over-reject. |
| 2 | **Volume is very uneven.** Days 0, 1 and 5 to 16 hold about 93% of rows (350k to 570k a day). Days 2 to 4 and 17 to 30 hold 1k to 57k, and day 30 holds 272 rows. Fraud stays at 216 to 320 rows a day. | The daily fraud rate is a simulation artifact (on day 30 all 272 rows are fraud). Dashboards carry a caveat, and time analyses lean on the heavy days. |
| 3 | **The built-in flag is nearly useless:** 16 of 8,213 fraud rows (0.19%). | ADR-005: flagged means DECLINED. The derived PaySim approval rate is 99.99975%, so real variation comes from generated days. |
| 4 | **A full-balance-drain rule fits almost perfectly.** 8,018 `TRANSFER` or `CASH_OUT` rows move the entire origin balance (`amount = oldbalanceOrg`), and all 8,018 are fraud (97.6% of all fraud). | A simple rule flag in gold. Labelled as a simulation artifact: real data would never be this clean. |
| 5 | **The 16 zero-amount rows are all fraud** (`CASH_OUT`). | Flag them, do not drop them. A naive `amount > 0` rule would silently delete fraud. |
| 6 | **Merchant balances are always 0** (all 2,151,495 rows), which means unknown. | Silver converts them to NULL. |
| 7 | **Almost every customer transacts once** (6.35M originators for 6.36M rows). | Per-customer activity flags need the generator's persistent population. Covering every PaySim ID in the SCD2 dimension means about 9.07M members (decided at the generator step). |
| 8 | **Type names use underscores** (`CASH_OUT`), unlike the dataset page text. | The data contract uses underscores. |
| 9 | **Balance columns are unreliable for fraud signals.** The dataset notes say detected fraud is cancelled, and 4,076 of 8,213 fraud rows have both destination balances at 0. | Balances are context only, never features. |

Rows per hour of day: near zero from 01:00 to 05:59, peaking at 17:00 to 19:59, while fraud is flat
across hours. The fraud rate is therefore highest overnight, partly an artifact.

## Time mapping (ADR-008)

Step 1 is 2026-08-20 00:00 and step 743 is 2026-09-19 22:00. Hour of day is `(step - 1) % 24`.
The England and Wales summer bank holiday (31 August) is day 11, a heavy day with 349k rows.
