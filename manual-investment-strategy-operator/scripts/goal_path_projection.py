#!/usr/bin/env python3
"""Calculate 5y/10y goal paths for the manual investment skill.

The script is intentionally small and data-source agnostic. It can consume a
portfolio snapshot JSON produced by portfolio_comparison_snapshot.py, or it can
run from explicit values when validating strategy assumptions offline.
"""

import argparse
import json
import sys


DEFAULT_RATES = [0.0, 0.15, 0.26, 0.35, 0.45, 0.58, 0.76]


def future_value(start_value, monthly_dca, annual_rate, years):
    months = years * 12
    if annual_rate == 0:
        return start_value + monthly_dca * months
    monthly_rate = (1 + annual_rate) ** (1 / 12) - 1
    return (
        start_value * (1 + monthly_rate) ** months
        + monthly_dca * (((1 + monthly_rate) ** months - 1) / monthly_rate)
    )


def required_annual_rate(start_value, monthly_dca, target_value, years):
    low = -0.99
    high = 5.0
    for _ in range(220):
        mid = (low + high) / 2
        value = future_value(start_value, monthly_dca, mid, years)
        if value < target_value:
            low = mid
        else:
            high = mid
    return high


def load_snapshot(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_rates(text):
    if not text:
        return DEFAULT_RATES
    return [float(part.strip()) for part in text.split(",") if part.strip()]


def build_projection(start_value, monthly_dca, rates):
    years_list = [5, 10]
    required = {}
    scenarios = []
    for years in years_list:
        current_target = start_value * 10
        cumulative_target = (start_value + monthly_dca * years * 12) * 10
        required[str(years)] = {
            "current_principal_10x_target": current_target,
            "current_principal_required_annual_rate": required_annual_rate(
                start_value, monthly_dca, current_target, years
            ),
            "cumulative_capital_10x_target": cumulative_target,
            "cumulative_capital_required_annual_rate": required_annual_rate(
                start_value, monthly_dca, cumulative_target, years
            ),
        }
        for annual_rate in rates:
            value = future_value(start_value, monthly_dca, annual_rate, years)
            scenarios.append(
                {
                    "years": years,
                    "annual_rate": annual_rate,
                    "future_value": value,
                    "current_principal_10x_gap": value - current_target,
                    "cumulative_capital_10x_gap": value - cumulative_target,
                }
            )
    return {
        "start_value": start_value,
        "monthly_dca": monthly_dca,
        "required": required,
        "scenarios": scenarios,
    }


def money(value):
    return f"${value:,.0f}"


def pct(value):
    return f"{value * 100:.1f}%"


def print_markdown(data):
    print("# Goal Path Projection")
    print()
    print(f"- Start value: `{money(data['start_value'])}`")
    print(f"- Monthly DCA: `{money(data['monthly_dca'])}`")
    print()
    print("## Required Annual Return")
    print()
    print("| Years | Current-principal 10x target | Required annual | Cumulative-capital 10x target | Required annual |")
    print("|---:|---:|---:|---:|---:|")
    for years in ["5", "10"]:
        row = data["required"][years]
        print(
            f"| {years} | {money(row['current_principal_10x_target'])} | "
            f"{pct(row['current_principal_required_annual_rate'])} | "
            f"{money(row['cumulative_capital_10x_target'])} | "
            f"{pct(row['cumulative_capital_required_annual_rate'])} |"
        )
    print()
    print("## Scenario Values")
    print()
    print("| Years | Annual return | Future value | Current 10x gap | Cumulative 10x gap |")
    print("|---:|---:|---:|---:|---:|")
    for row in data["scenarios"]:
        print(
            f"| {row['years']} | {pct(row['annual_rate'])} | "
            f"{money(row['future_value'])} | {money(row['current_principal_10x_gap'])} | "
            f"{money(row['cumulative_capital_10x_gap'])} |"
        )


def main():
    parser = argparse.ArgumentParser(description="Project 5y/10y 10x goal paths.")
    parser.add_argument("--snapshot-json", help="Path to portfolio snapshot JSON, or '-' for stdin.")
    parser.add_argument("--portfolio-value", type=float, help="Current total portfolio value.")
    parser.add_argument("--monthly-dca", type=float, default=1000.0)
    parser.add_argument("--rates", help="Comma-separated annual rates, e.g. 0,0.15,0.26.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = parser.parse_args()

    if args.snapshot_json:
        snapshot = load_snapshot(args.snapshot_json)
        start_value = float(snapshot["totals"]["current"]["total_value"])
    elif args.portfolio_value is not None:
        start_value = args.portfolio_value
    else:
        parser.error("Provide --snapshot-json or --portfolio-value.")

    data = build_projection(start_value, args.monthly_dca, parse_rates(args.rates))
    if args.format == "json":
        json.dump(data, sys.stdout, indent=2, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print_markdown(data)


if __name__ == "__main__":
    main()
