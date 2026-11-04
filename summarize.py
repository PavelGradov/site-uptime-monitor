#!/usr/bin/env python3
"""Summarize recorded check success rates (not continuous site uptime)."""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

parser = argparse.ArgumentParser(description="Summarize site checks by UTC date")
parser.add_argument("--folder", default="logs")
parser.add_argument("--last", type=int, default=7)
args = parser.parse_args()

counts = defaultdict(lambda: [0, 0])
for filepath in sorted(Path(args.folder).glob("????-??-??.csv")):
    with filepath.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            day = row["checked_at_utc"][:10]
            counts[day][0] += 1
            counts[day][1] += row["status"] == "up"
print("UTC date       Checks    Up     Observed success rate")
for day in sorted(counts)[-args.last:]:
    checked, up = counts[day]
    print(f"{day}    {checked:5d}   {up:5d}     {100 * up / checked:.2f}%")
if not counts:
    print("No checks recorded yet.")
print("Note: percentage of successful checks is not continuous uptime.")
