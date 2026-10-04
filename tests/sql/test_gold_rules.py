"""The rule views: how good each rule is (precision and recall), and which entities look unusual.

Both read the fact's rule flags. The fact rows are built directly, so a failure here means the rule
view is wrong, not the flags. Thresholds are written at the top of unusual_activity.sql, and these
tests pin the edge of each one (7 transactions is quiet, 8 is high frequency).
"""

import pytest

from tests.helpers.lakeflow_sql import find, load
from tests.helpers.spark_support import make_table, register_query, run_query, violations
from tests.sql.fixtures import FACT, fact

pytestmark = pytest.mark.sql


def view(spark, file, name, fact_rows):
    make_table(spark, "workspace.gold.fact_transactions", FACT, list(fact_rows))
    register_query(spark, "gold/dim_type.sql", "workspace.gold.dim_type", "gold_dim_type")
    return run_query(spark, file, name)


# ---- rule_effectiveness -------------------------------------------------------------------------

# Nine generator rows. Fraud: R1, R2, R3, R5, R7 (five in all).
#   balance drain flags R1 to R4        (3 of the 4 are fraud)
#   night high value flags R5           (fraud)
#   burst flags R6                      (not fraud)
#   source rule flags R9                (not fraud)
RULES = [
    fact(event_id="R1", is_fraud=True, flag_balance_drain=True),
    fact(event_id="R2", is_fraud=True, flag_balance_drain=True),
    fact(event_id="R3", is_fraud=True, flag_balance_drain=True),
    fact(event_id="R4", flag_balance_drain=True),
    fact(event_id="R5", is_fraud=True, flag_night_high_value=True),
    fact(event_id="R6", flag_burst=True),
    fact(event_id="R7", is_fraud=True),
    fact(event_id="R8"),
    fact(event_id="R9", flag_source_rule=True),
]


def rule_table(spark, fact_rows):
    rows = view(
        spark, "gold/rule_effectiveness.sql", "workspace.gold.rule_effectiveness", fact_rows
    )
    return {(r["source_system"], r["rule_name"]): r for r in rows}


def test_precision_is_the_share_of_flagged_rows_that_are_fraud_and_recall_the_share_of_fraud_found(
    spark,
):
    rules = rule_table(spark, RULES)
    drain = rules[("generator-v1", "balance_drain")]
    assert (drain["flagged_count"], drain["fraud_caught"], drain["fraud_total"]) == (4, 3, 5)
    assert drain["precision_rate"] == pytest.approx(0.75)
    assert drain["recall_rate"] == pytest.approx(0.6)


def test_each_rule_is_measured_on_its_own_flag(spark):
    rules = rule_table(spark, RULES)
    night = rules[("generator-v1", "night_high_value")]
    assert (night["flagged_count"], night["precision_rate"], night["recall_rate"]) == (1, 1.0, 0.2)
    burst = rules[("generator-v1", "burst")]
    assert (burst["flagged_count"], burst["fraud_caught"], burst["precision_rate"]) == (1, 0, 0.0)
    source = rules[("generator-v1", "source_rule")]
    assert (source["flagged_count"], source["recall_rate"]) == (1, 0.0)


def test_the_combined_rule_is_our_three_rules_and_leaves_out_the_sources_own(spark):
    combined = rule_table(spark, RULES)[("generator-v1", "any_of_our_rules")]
    # R1 to R6 are flagged by one of ours (R9 only by the source's rule); four of them are fraud.
    assert (combined["flagged_count"], combined["fraud_caught"]) == (6, 4)
    assert combined["precision_rate"] == pytest.approx(4 / 6)
    assert combined["recall_rate"] == pytest.approx(0.8)


def test_a_rule_that_flags_nothing_has_no_precision_and_does_not_divide_by_zero(spark):
    rules = rule_table(spark, [fact(event_id="A", source_system="paysim", is_fraud=True)])
    drain = rules[("paysim", "balance_drain")]
    assert drain["flagged_count"] == 0 and drain["precision_rate"] is None
    assert drain["recall_rate"] == 0.0  # one fraud row, none found


def test_no_fraud_and_no_flags_gives_no_ratios_at_all(spark):
    drain = rule_table(spark, [fact(event_id="A")])[("generator-v1", "balance_drain")]
    assert drain["precision_rate"] is None and drain["recall_rate"] is None


def test_each_source_is_measured_on_its_own(spark):
    rules = rule_table(
        spark,
        [
            fact(event_id="G", is_fraud=True, flag_balance_drain=True),
            fact(event_id="P", source_system="paysim", flag_balance_drain=True),
        ],
    )
    assert rules[("generator-v1", "balance_drain")]["precision_rate"] == 1.0
    assert rules[("paysim", "balance_drain")]["precision_rate"] == 0.0


# ---- unusual_activity ---------------------------------------------------------------------------


def unusual(spark, fact_rows):
    rows = view(spark, "gold/unusual_activity.sql", "workspace.gold.unusual_activity", fact_rows)
    return {(r["entity_role"], r["entity_id"]): r for r in rows}


def many(count, **overrides):
    return [
        fact(event_id=f"{overrides.get('customer_id', 'X')}-{i}", **overrides) for i in range(count)
    ]


def test_eight_transactions_in_a_day_is_high_frequency_and_seven_is_not(spark):
    flagged = unusual(spark, many(8, customer_id="BUSY") + many(7, customer_id="QUIET"))
    assert flagged[("sender", "BUSY")]["flag_reasons"] == ["HIGH_FREQUENCY"]
    assert ("sender", "QUIET") not in flagged


def test_a_burst_or_a_balance_drain_flags_the_sender_even_for_one_transaction(spark):
    flagged = unusual(
        spark,
        [
            fact(event_id="B", customer_id="BURSTY", flag_burst=True),
            fact(event_id="D", customer_id="DRAINED", flag_balance_drain=True),
        ],
    )
    assert flagged[("sender", "BURSTY")]["flag_reasons"] == ["BURST"]
    assert flagged[("sender", "DRAINED")]["flag_reasons"] == ["BALANCE_DRAIN"]


def test_several_reasons_are_listed_in_a_fixed_order(spark):
    rows = many(8, customer_id="ALL", flag_burst=True, flag_balance_drain=True)
    assert unusual(spark, rows)[("sender", "ALL")]["flag_reasons"] == [
        "HIGH_FREQUENCY", "BURST", "BALANCE_DRAIN",
    ]  # fmt: skip


def test_a_recipient_of_five_transfers_from_five_different_senders_is_a_hub(spark):
    rows = [
        fact(event_id=f"H{i}", customer_id=f"SENDER{i}", counterparty_id="HUB") for i in range(5)
    ]
    assert unusual(spark, rows)[("recipient", "HUB")]["flag_reasons"] == ["HUB_RECIPIENT"]


def test_five_transfers_from_only_four_senders_is_not_a_hub(spark):
    senders = ["S1", "S2", "S3", "S4", "S4"]
    rows = [
        fact(event_id=f"H{i}", customer_id=s, counterparty_id="HUB") for i, s in enumerate(senders)
    ]
    assert ("recipient", "HUB") not in unusual(spark, rows)


def test_only_transfers_count_towards_a_hub(spark):
    rows = [
        fact(event_id=f"C{i}", customer_id=f"SENDER{i}", counterparty_id="HUB", type_key=2)
        for i in range(5)
    ]  # five CASH_OUT rows to the same recipient
    assert ("recipient", "HUB") not in unusual(spark, rows)


def test_each_day_is_judged_on_its_own(spark):
    rows = many(5, customer_id="DAYA") + [
        fact(event_id=f"D{i}", customer_id="DAYA", date_key=20261007) for i in range(5)
    ]
    assert ("sender", "DAYA") not in unusual(spark, rows)  # 10 rows over two days, 5 each


def test_every_listed_entity_has_a_reason(spark):
    rows = many(8, customer_id="BUSY") + many(2, customer_id="QUIET")
    make_table(spark, "workspace.gold.fact_transactions", FACT, rows)
    register_query(spark, "gold/dim_type.sql", "workspace.gold.dim_type", "gold_dim_type")
    register_query(spark, "gold/unusual_activity.sql", "workspace.gold.unusual_activity", "flagged")
    statement = find(load("gold/unusual_activity.sql"), "workspace.gold.unusual_activity")
    assert [e.name for e in statement.expectations] == ["has_reason"]
    assert violations(spark, "flagged", statement.expectations[0]) == []
    assert spark.table("flagged").count() == 1
