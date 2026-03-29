"""
schedule_task.py — Register (or update) the daily briefing task in
Windows Task Scheduler.

Usage:
  python schedule_task.py            # schedules at 06:00 by default
  python schedule_task.py --time 07:30
  python schedule_task.py --remove   # delete the task

The task runs main.py with the current Python interpreter every day at the
specified time, regardless of whether a user is logged in (requires the script
to be run once as Administrator to enable the "run whether logged on or not"
setting).
"""

import argparse
import subprocess
import sys
from pathlib import Path

import config

TASK_NAME = "DailyIntelligenceBriefing"


def _python() -> str:
    """Return the absolute path to the current Python interpreter."""
    return sys.executable


def _project_dir() -> Path:
    return Path(__file__).resolve().parent


def create_task(run_time: str) -> None:
    """
    Register or overwrite the Task Scheduler entry.

    Args:
        run_time: 24-hour time string, e.g. "06:00" or "07:30".
    """
    python    = _python()
    main_py   = _project_dir() / "main.py"
    start_dir = str(_project_dir())

    # schtasks /Create registers (or replaces with /F) the task.
    # DAILY trigger at the given time; runs under the current user's account.
    cmd = [
        "schtasks", "/Create", "/F",
        "/TN",  TASK_NAME,
        "/TR",  f'"{python}" "{main_py}"',
        "/SC",  "DAILY",
        "/ST",  run_time,
        "/SD",  "01/01/2000",          # start date in the past = active immediately
    ]

    print(f"Registering task '{TASK_NAME}' to run daily at {run_time}...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"Task registered successfully.")
        print(f"  Name:       {TASK_NAME}")
        print(f"  Time:       {run_time} daily")
        print(f"  Command:    {python} {main_py}")
        print(f"  Directory:  {start_dir}")
        print()
        print("To run as a background service (no console window), re-run this")
        print("script once from an elevated (Administrator) command prompt.")
    else:
        print(f"[ERROR] schtasks failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)


def remove_task() -> None:
    """Delete the Task Scheduler entry."""
    cmd = ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"Task '{TASK_NAME}' removed.")
    else:
        print(f"[ERROR] Could not remove task:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)


def show_status() -> None:
    """Print the current Task Scheduler entry for this task."""
    cmd = ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"Task '{TASK_NAME}' is not currently registered.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Manage the daily intelligence briefing schedule in Windows Task Scheduler.",
    )
    parser.add_argument(
        "--time",
        metavar="HH:MM",
        default=config.DAILY_RUN_TIME,
        help=f"24-hour time to run the briefing each morning (default: {config.DAILY_RUN_TIME}).",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove the scheduled task.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show the current task status.",
    )
    args = parser.parse_args()

    if args.remove:
        remove_task()
    elif args.status:
        show_status()
    else:
        create_task(args.time)
