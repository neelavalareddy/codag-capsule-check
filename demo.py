"""Run the full capsule-check suite and print the report (SYNTHETIC data).

Usage: python demo.py [--out reports] [--write-data data/sessions]
"""
import argparse

from capsule_check.dataset import write_dataset
from capsule_check.report import run_suite, to_markdown, write_reports


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports")
    ap.add_argument("--write-data", default=None)
    a = ap.parse_args()
    if a.write_data:
        write_dataset(a.write_data)
    report = run_suite()
    write_reports(report, a.out)
    print(to_markdown(report))
    print("wrote %s/report.md and %s/report.json" % (a.out, a.out))


if __name__ == "__main__":
    main()
