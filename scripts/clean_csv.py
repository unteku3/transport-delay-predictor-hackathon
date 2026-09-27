#!/usr/bin/env python3
"""Export raw + cleaned measurements and quality flags without model artifacts."""
from __future__ import annotations
import argparse
import csv
from dataclasses import replace
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cleaning.replay import decisions, read_packets
from cleaning.types import Config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'cleaning/config.json')
    parser.add_argument('--filter-speed', action='store_true', help='Reject high speed corroborated by GPS disagreement')
    parser.add_argument('--quarantine-gps', action='store_true', help='Quarantine implausible GPS jumps')
    parser.add_argument('--detect-stops', action='store_true', help='Require spatial compactness for stationary state')
    args = parser.parse_args()
    original = {p.resolve() for folder in ['train', 'test', 'validate', 'labels']
                for p in (ROOT / folder).glob('*.csv')}
    if args.output.resolve() in original or args.input.resolve() == args.output.resolve():
        raise ValueError('Output must not overwrite source data')
    if args.output.exists():
        raise FileExistsError('Choose a new output file')
    config = Config(**json.loads(args.config.read_text()))
    config = replace(config, **{name: True for name in ['filter_speed', 'quarantine_gps', 'detect_stops']
                                if getattr(args, name)})
    packets = read_packets(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if args.output.suffix == '.gz' else open
    with opener(args.output, 'xt', newline='') as f:
        writer = None
        for decision in decisions(packets, config):
            row = decision.as_row()
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=list(row))
                writer.writeheader()
            writer.writerow(row)


if __name__ == '__main__':
    main()
