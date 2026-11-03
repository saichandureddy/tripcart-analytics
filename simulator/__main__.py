"""Run the simulator: python -m simulator  (or `make simulate`).

Generates everything that happened since the last run. The first run creates
the app database tables and backfills a few months of history.
"""

import argparse
import time

import psycopg

from ingestion import settings
from simulator.engine import Simulator, load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=None, help="random seed, for repeatable data")
    args = parser.parse_args()

    started = time.monotonic()
    with psycopg.connect(settings.app_db_url(), autocommit=True) as conn:
        sim = Simulator(conn, settings.s3_client(), settings.s3_bucket(), load_config(), seed=args.seed)
        summary = sim.run()

    print(
        f"Simulated {summary.pop('from'):%Y-%m-%d %H:%M} -> {summary.pop('to'):%Y-%m-%d %H:%M} UTC "
        f"in {time.monotonic() - started:.0f}s"
    )
    if not summary:
        print("  Nothing new: the last run was moments ago.")
    for key, value in sorted(summary.items()):
        print(f"  {key:<28} {value:>8}")


if __name__ == "__main__":
    main()
