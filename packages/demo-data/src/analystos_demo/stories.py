"""Planted analytical stories: the knobs the generator turns, and their target magnitudes.

The generator applies these as real causal changes (fewer orders, cheaper tier mix, more
discount, higher unit cost, stalled SKUs, unworked leads). Two knobs are calibrated by
deterministic bisection so the headline magnitudes land exactly on target; every number in
the answer key (`scenarios.json`) is then re-measured from the written files with DuckDB.
"""

from __future__ import annotations

START = "2024-10-01"
END = "2026-09-30"

# --- Story 1: August 2026 revenue decline (vs July 2026) -------------------------------
AUG_2026 = "2026-08"
JUL_2026 = "2026-07"
TARGET_AUG_VS_JUL = -0.118
MAJOR_CUSTOMER_ID = "C-10001"
MAJOR_CUSTOMER_NAME = "Trinity Ridge Construction"
MAJOR_CUSTOMER_AUG_ORDER_FACTOR = 0.17  # the customer moved most of its volume to a competitor
MAJOR_CUSTOMER_LATER_ORDER_FACTOR = 0.3  # and has not come back in September
DALLAS_AUG_REVENUE_CHANGE = (
    -0.15
)  # Dallas (excluding the major customer): fewer orders after a rival opened nearby
OTHER_BRANCH_SPREAD = 0.015  # branch-to-branch spread around the broad-based change elsewhere
JUL_AUG_OVERSUPPLY = 1.12  # candidate orders drawn for Jul/Aug, then thinned per branch to the targets
AUG_TIER_SHIFT = (0.07, -0.04, -0.03)  # Entry / Standard / Premium probability shift
AUG_PROMO_LINE_SHARE = 0.40  # share of August lines carrying the late-summer promotion
AUG_PROMO_EXTRA_DISCOUNT = 0.05
AUG_INSULATION_WEIGHT_FACTOR = 1.35  # insulation share of August lines (energy-code rebate program)
INSULATION_MONTHLY_TREND_2026 = 0.03  # insulation line-share growth per month through 2026

# --- Story 2: margin compression, Q2 2026 vs Q2 2025 (revenue flat) ---------------------
Q2_2026 = ("2026-04-01", "2026-06-30")
Q2_2025 = ("2025-04-01", "2025-06-30")
TARGET_Q2_REVENUE_YOY = 0.003
COST_SHOCK = {  # unit-cost increase by category, ramped in by month (list prices not raised)
    "Roofing": {"2026-03": 0.03, "2026-04": 0.05, "2026-05": 0.07},
}
SUPPLIER_COST_SHOCK = {  # (category, supplier): overrides COST_SHOCK for that supplier's products
    ("Lumber", "Gulf States Forest Products"): {"2026-03": 0.12},  # mill curtailment, one price step
    ("Lumber", "Piney Woods Timber"): {"2026-03": 0.01, "2026-04": 0.02, "2026-05": 0.025},
}
CONTRACTOR_DISCOUNT_UPLIFT = {  # Contractor discounting from April 2026 to defend volume,
    "Gulf Coast": 0.08,  # deepest where the housing slowdown hit hardest
    "default": 0.02,
}
CONTRACTOR_DISCOUNT_FROM = "2026-04"

# --- Story 0: operating profit below the FY26 Operating Plan, Q2 2026 (revenue on plan) ----
PLAN_VERSION = "FY26 Operating Plan"
PLAN_YEAR = 2026
PLAN_BASE_YEAR = 2025
PLAN_LIST_PRICE_INCREASE = 0.015  # January 2026 list-price increase (it happened as planned)
PLAN_COST_INFLATION = 0.02  # planned supplier cost inflation, every category
PLAN_VOLUME_GROWTH = {"Enterprise": 0.03, "Retail": -0.03}  # Contractor is solved (see below)
TARGET_Q2_REVENUE_VS_PLAN = 0.004  # Q2 revenue lands this far above plan: the target is "hit"
# Contractor volume growth is solved so the Q2 plan sits TARGET_Q2_REVENUE_VS_PLAN below the
# calibrated Q2 actual (Q2 2025 x (1 + TARGET_Q2_REVENUE_YOY)): the plan anticipated a housing
# slowdown, just not one as deep as Gulf Coast and Central Texas turned out to be.
PLAN_FPA_ADJUSTMENT_SD = 0.01  # FP&A judgement per branch x segment
NEW_STORE_RUN_RATE_MONTHS = ("2025-09", "2025-10", "2025-11")  # a store opened in FY25 is planned
NEW_STORE_RAMP = 0.5  # at its Sep-Nov 2025 run rate (seasonally adjusted) plus this ramp
PLAN_OPEX_GROWTH = {  # FY26 opex lines: prior-year branch actual x (1 + growth)
    "payroll": 0.03,  # merit increase
    "overtime": 0.0,
    "occupancy": 0.025,  # lease escalators
    "other_opex": 0.02,
}
PLAN_FREIGHT_RATE_INCREASE = 0.02  # freight planned at the prior-year freight/revenue ratio + 2%
SOFT_REGIONS = ("Gulf Coast", "Central Texas")  # Contractor housing slowdown, calibrated knob
FUEL_SURCHARGE_SPIKE = 0.40  # carrier fuel surcharge as a share of base freight, from April 2026
FUEL_SURCHARGE_FROM = "2026-04"
OVERTIME_STORY_BRANCHES = ("BR-SAT", "BR-AUS")  # unfilled yard positions covered with overtime
OVERTIME_STORY_FROM = "2026-04"
OPEN_POSITIONS = 2  # yard positions left unfilled per story branch (headcount below plan)
OVERTIME_STORY_MONTHLY = 47_000.0  # extra overtime per story branch per month

# --- Story 3: conversion decline, Q2 2026 vs Q1 2026 leads -----------------------------
CONVERSION_STORY_REGION = "Gulf Coast"
CONVERSION_STORY_CHANNEL = "Paid Search"
CONVERSION_STORY_FROM = "2026-04-01"
CONVERSION_STORY_FACTOR = 0.2  # leads routed to an unmonitored queue after a CRM change
LEADS_PER_MONTH = 640

# --- Story 4: inventory build-up, 2026-06-30 vs 2025-06-30 -----------------------------
SLOW_SKU_COUNT = 15
SLOW_SKU_LAUNCH = "2025-09-15"
SLOW_SKU_BUILD_FROM = "2025-10"  # purchasing kept replenishing to a forecast that never came
SLOW_SKU_TARGET_SHARE_OF_BASE = 0.2  # extra inventory value by 2026-06-30 vs total value at 2025-06-30
