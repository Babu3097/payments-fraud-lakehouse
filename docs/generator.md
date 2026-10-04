# Daily feed generator

PaySim is a one-off historical file and is too clean (see [data_profile.md](data_profile.md)).
The generator supplies what PaySim lacks: new files every day, customers who transact repeatedly,
a customer attribute history for SCD Type 2, an approved or declined status, and known defects for
the quality layer to catch. Code: `src/payments_lakehouse/generator.py`. Tests:
`tests/test_generator.py`.

```bash
uv run python -m payments_lakehouse.generator --start 2026-09-20 --end 2026-10-03 --out data/generated
```

## Files per day

| File | Format | Contents |
|---|---|---|
| `transactions_<date>.jsonl` | JSON Lines | About 40,000 transactions plus injected defects |
| `customer_profile_<date>.csv` | CSV | Customer and merchant change events. 65,000 initial rows on 2026-09-20, then 325 a day |
| `manifest_<date>.json` | JSON | Ground truth: exact counts of rows, fraud, declines and each defect |

## Transaction contract (version 1)

`event_id` (unique per event), `event_ts` (UTC, `YYYY-MM-DDTHH:MM:SSZ`), `type` (`CASH_OUT`,
`PAYMENT`, `CASH_IN`, `TRANSFER`, `DEBIT`), `amount`, `customer_id`, `counterparty_id`,
`origin_balance_before`, `origin_balance_after`, `status` (`APPROVED` or `DECLINED`),
`decline_reason` (null when approved), `is_fraud` (0 or 1), `source_system`.
From **2026-09-28** each row also carries `channel` (`app`, `agent`, `pos`): a deliberate schema
change, so Auto Loader's schema evolution has something real to handle.

Balances are a synthetic snapshot per row, not a ledger.

## Injected defects (per 40,000 clean rows)

| Defect | Rate | Rows | How it looks |
|---|---|---|---|
| Null in a required field | 0.5% | 200 | One of `customer_id`, `amount`, `type`, `event_ts` is null |
| Bad amount | 0.3% | 120 | Negative amount |
| Unknown type | 0.2% | 80 | `REFUND`, `CHARGEBACK` or `ADJUSTMENT` |
| Bad timestamp | 0.1% | 40 | Text that is not a valid timestamp |
| Duplicate | 1.0% | 400 | Exact copy of a clean row, like an upstream retry |

Each defect lands on a different row, so every count is exact. Silver should quarantine 440
rows a day (200 + 120 + 80 + 40) and drop 400 duplicates.

## Behaviour

- **Population:** 60,000 customers (`CG0000001` onwards) and 5,000 merchants (`MG000001` onwards).
  The prefixes never collide with PaySim's `C...` and `M...` IDs. Activity is skewed, so some
  customers are busier than others.
- **Mix:** transaction types and the hour-of-day curve follow the PaySim profile.
- **Declines:** about 3% of normal rows, with reasons `INSUFFICIENT_FUNDS`, `LIMIT_EXCEEDED`,
  `CARD_BLOCKED`.
- **Fraud:** 0.3% of rows in three patterns: a transfer or cash-out that empties the whole balance
  (60%), a high-value night-time transfer (20%), and a burst of six transfers within about ten
  minutes (20%). About 40% is caught (declined as `SUSPECTED_FRAUD`) and 60% is approved and missed.
  Twenty fixed mule accounts receive most of the fraudulent transfers.
- **Customer profile:** each change event is a full row (segment and region) and alters exactly one
  attribute. `changed_at` is the sequence Auto CDC uses to build the SCD2 history.

## Verified on the 14 generated days (2026-09-20 to 2026-10-03)

560,000 unique events (565,600 lines with duplicates), approval rate 96.87%, fraud 0.30% (981
approved, 699 declined), 366 customer-days with six or more transactions, 69,225 profile rows.

## How it is tested

Eleven unit tests, including one that checks the manifest against independent counts taken from
the file itself. As a check on the tests, five deliberate bugs were introduced in a scratch copy
(unseeded randomness, a miscounted duplicate total, a fraud row that no longer empties the
balance, the channel field arriving a day late, a profile change that changes nothing). Each was
caught by exactly the intended test.
