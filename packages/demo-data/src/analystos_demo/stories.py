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
    "Lumber": {"2026-03": 0.03, "2026-04": 0.06, "2026-05": 0.09},
    "Roofing": {"2026-03": 0.03, "2026-04": 0.05, "2026-05": 0.07},
}
CONTRACTOR_DISCOUNT_UPLIFT = 0.035  # Contractor discounting from April 2026 to defend volume
CONTRACTOR_DISCOUNT_FROM = "2026-04"

# --- Story 5: forecast miss, Q2 2026 actual vs FY26 plan (driven by Contractor softness) --
FORECAST_GROWTH = 0.05
FORECAST_VERSION = "FY26 Plan (Dec 2025)"
SOFT_REGIONS = ("Gulf Coast", "Central Texas")  # Contractor housing slowdown, calibrated knob

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
