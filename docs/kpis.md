# KPI catalogue

How each question from the project brief is answered, with exact definitions and the caveats a
reader needs. Everything is in the `workspace.gold` schema and is rebuilt on every update.

| Question | Where it is answered |
|---|---|
| Daily volume, value, approval and fraud rates | `kpi_daily` |
| Fraud by transaction type | `kpi_fraud_by_type` |
| Fraud by amount band | `kpi_fraud_by_amount_band` |
| Fraud by time of day | `kpi_fraud_by_hour` (hour and day part) |
| Customers and merchants with unusual activity | `unusual_activity` |
| How good are the simple rules? | `rule_effectiveness` |
| Do gold totals match the raw source? | `reconciliation` (inside the pipeline) and `sql/checks/gold_reconciliation.sql` |

Every KPI table keeps `source_system` (`paysim` or `generator-v1`) apart, because the two sources
behave very differently (see the caveats below).

## Definitions

| KPI | Definition |
|---|---|
| Volume | Number of transactions (`txn_count`) |
| Value | Sum of `amount` (`txn_value`), in the source's local currency |
| Approval rate | Approved transactions divided by all transactions. A transaction is declined when `status = 'DECLINED'` |
| Fraud rate | Fraud transactions divided by all transactions, using the label `is_fraud` |
| Fraud caught | Fraud transactions that were declined |
| Fraud missed | Fraud transactions that were approved (`fraud_missed_count`, `fraud_missed_value`) |
| Fraud catch rate | Fraud caught divided by all fraud |
| Rule precision | Of the rows a rule flags, the share that really are fraud |
| Rule recall | Of all fraud rows, the share the rule flags |

Rows that failed a quality check are in `silver.transactions_quarantine`, not in gold, so they are
**excluded** from every KPI until repaired (17 fraud events were in quarantine at the first check).

## Verified results (first gold run: 15 generated days, 20 Sep to 4 Oct, plus the PaySim month)

| Measure | PaySim | Generated feed |
|---|---|---|
| Approval rate | about 100%, derived from PaySim's own flag, so almost constant | 96.8% to 96.9% a day |
| Fraud rate | 0.13% overall, very uneven by day | 0.30% a day |
| Fraud approved (missed) | 8,197 of 8,213 | about 60% of fraud |
| Highest fraud rate by type | `TRANSFER` 0.77% | `TRANSFER` 2.45% |

Rule effectiveness (`rule_effectiveness`):

| Rule | PaySim precision / recall | Generated precision / recall |
|---|---|---|
| Source's own rule | 100% / 0.19% | none |
| Balance drain | 100% / 97.6% | 100% / 59.9% |
| Burst (5+ transfers in 15 minutes) | none occur | 100% / 20.1% |
| Night-time high-value transfer | 44% / 8.1% | 97% / 21.0% |
| Any of our rules | 90.6% / 97.9% | 99.4% / 96.8% |

## Caveats

1. **PaySim's daily fraud rate is a simulation artifact.** Legitimate volume collapses after day 16
   while fraud stays at about 250 a day, so on 19 September all 272 transactions are fraud (rate
   100%). Do not read a trend into it. Time analyses lean on the heavy days.
2. **PaySim's approval rate is derived** (ADR-005) and is almost constant. The generator's
   approval rate is a real, varying KPI.
3. **The rule numbers flatter the rules.** PaySim's balance-drain pattern is an artifact of the
   simulation, and the generated fraud was built from the same three patterns the rules look for.
   The night-time rule on PaySim (44% precision) is the realistic number.
4. **The overnight fraud rate is partly an artifact:** fraud is spread evenly across the day while
   legitimate traffic is quiet overnight, so the rate is highest at night.
5. **`unusual_activity` lists leads, not verdicts.** Thresholds are written at the top of the file.
   A flagged day for a high-frequency sender is often a busy customer, not fraud.
