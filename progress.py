"""
progress.py — Rich terminal progress bar and logging configuration.

Provides:
  BriefingProgress    — live pipeline progress bar wrapping rich.progress.Progress.
                        Each pipeline step is an async context manager that times
                        the step, updates the bar, and logs start/done lines.
  build_pipeline_steps — factory that returns the correct ordered step list based
                         on feature flags (GENERATE_TRADES, summarizer, Gmail).
  setup_rich_logging  — replaces the plain StreamHandler with a RichHandler so
                        console output is coloured and levelled, while the file
                        log remains unchanged plain text.

Usage in main.py:
    _setup_logging(target_date)          # calls setup_rich_logging internally
    steps = build_pipeline_steps(...)
    async with BriefingProgress(steps) as bp:
        async with bp.step("init_db"):
            init_database()
        async with bp.step("phase_a"):
            ...
"""

import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

# Single shared Console instance.  Both RichHandler and Progress must reference
# the same object so rich can coordinate the live display and log messages
# without clobbering each other.
_console = Console(highlight=False)


# ── Step definition ───────────────────────────────────────────────────────────

@dataclass
class PipelineStep:
    key: str
    label: str


def build_pipeline_steps(
    *,
    generate_trades: bool,
    summarizer_enabled: bool,
    will_unlabel: bool,
) -> list[PipelineStep]:
    """
    Return the ordered list of pipeline steps for this run.

    Conditional steps (trades, summarizer, unlabelling) are included or
    excluded so the progress bar's M/N count is accurate from the start.

    Args:
        generate_trades:    config.GENERATE_TRADES
        summarizer_enabled: config.SUMMARIZER_ENABLED
        will_unlabel:       True iff purge_labels AND intelligence_filepath is None
    """
    steps: list[PipelineStep] = [
        PipelineStep("init_db",  "Init database"),
        PipelineStep("ingest",   "Ingest intelligence"),
    ]
    if summarizer_enabled:
        steps.append(PipelineStep("summarize", "Multi-stage summarization"))
    steps += [
        PipelineStep("phase_a",  "Phase A  — appendix ‖ themes ‖ market data"),
        PipelineStep("between",  "Between  — historical context + financial data"),
        PipelineStep("phase_b",  "Phase B  — opening ‖ adversarial ‖ strategic"),
    ]
    if generate_trades:
        steps += [
            PipelineStep("positional",   "Positional trades  (map-reduce ×2)"),
            PipelineStep("tactical",     "Tactical trades    (map-reduce ×3)"),
            PipelineStep("hydration",    "Supplemental hydration"),
            PipelineStep("phase_c",      "Phase C  — quant analysis (concurrent)"),
            PipelineStep("logic_review", "Phase C  — trade logic review"),
        ]
    steps += [
        PipelineStep("compile",  "Compile HTML + aggregate JSON"),
        PipelineStep("save",     "Save HTML + JSON"),
        PipelineStep("memory",   "Commit to longitudinal memory"),
        PipelineStep("deliver",  "Render PDF + deliver email"),
    ]
    if will_unlabel:
        steps.append(PipelineStep("unlabel", "Unlabel ingested emails"))
    return steps


# ── Progress bar ──────────────────────────────────────────────────────────────

class BriefingProgress:
    """
    Live pipeline progress bar for the briefing system.

    Wraps rich.progress.Progress to show a single progress bar that advances
    as each named pipeline step completes.  Each step is timed; start and
    completion are logged via the "briefing" logger so they appear in both
    the rich console output and the plain-text file log.

    Must be used as an async context manager.  Individual steps are entered
    via the async context manager returned by .step(key).

    Example:
        async with BriefingProgress(steps) as bp:
            async with bp.step("init_db"):
                init_database()
            async with bp.step("phase_a"):
                results = await asyncio.gather(...)
    """

    def __init__(self, steps: list[PipelineStep]) -> None:
        self._steps     = steps
        self._step_map  = {s.key: s for s in steps}
        self._times:   dict[str, float] = {}
        self._completed = 0
        self._logger    = logging.getLogger("briefing")

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description:<58}"),
            BarColumn(bar_width=22),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            console=_console,
            transient=False,
        )
        self._task_id = None

    async def __aenter__(self) -> "BriefingProgress":
        self._progress.start()
        self._task_id = self._progress.add_task(
            description="Starting…",
            total=len(self._steps),
            completed=0,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._task_id is not None:
            if exc_type is None:
                self._progress.update(
                    self._task_id,
                    description="[bold green]Pipeline complete",
                    completed=len(self._steps),
                )
            else:
                self._progress.update(
                    self._task_id,
                    description="[bold red]Pipeline failed",
                )
        self._progress.stop()
        if exc_type is None:
            _console.print(self.summary_table())
        return False  # never suppress exceptions

    @asynccontextmanager
    async def step(self, key: str):
        """
        Async context manager for one named pipeline step.

        On entry:  logs "▶ label", updates the progress bar description.
        On exit:   records elapsed time, advances the completed count,
                   logs "✓ label — Xs".
        """
        step  = self._step_map.get(key)
        label = step.label if step else key

        if self._task_id is not None:
            self._progress.update(self._task_id, description=label)
        self._logger.info("  ▶  %s", label)

        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - t0
            self._times[key] = elapsed
            self._completed += 1
            self._logger.info("  ✓  %s — %.1fs", label, elapsed)
            if self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    completed=self._completed,
                    description=f"[dim]{label}[/dim]",
                )

    def summary_table(self) -> Table:
        """Rich Table summarising each step's wall-clock duration."""
        table = Table(
            title="[bold]Pipeline Step Summary",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            min_width=52,
        )
        table.add_column("#",        style="dim",   justify="right", width=3)
        table.add_column("Step",     style="cyan")
        table.add_column("Duration", style="green", justify="right", width=10)
        for i, step in enumerate(self._steps, 1):
            t = self._times.get(step.key)
            duration = f"{t:.2f}s" if t is not None else "[dim]skipped[/dim]"
            table.add_row(str(i), step.label, duration)
        return table


# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_rich_logging(logger: logging.Logger, log_file_path: Path) -> None:
    """
    Configure the logger for rich console output and plain-text file logging.

    Console: RichHandler — coloured, levelled, timestamped.
             Uses the module-level _console so it coordinates with
             BriefingProgress's live display without display artifacts.
    File:    FileHandler — plain text, same format as before.

    Idempotent — safe to call multiple times; handlers are only added once.
    """
    if logger.handlers:
        return

    logger.setLevel(logging.DEBUG)

    # Console — rich colours, no path column, no markup injection from user strings
    console_handler = RichHandler(
        console=_console,
        show_time=True,
        show_path=False,
        markup=False,
        rich_tracebacks=True,
        log_time_format="%H:%M:%S",
    )
    console_handler.setLevel(logging.DEBUG)
    logger.addHandler(console_handler)

    # File — plain text, unchanged format
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    logger.info("Log file: %s", log_file_path.resolve())
