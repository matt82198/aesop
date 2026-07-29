"""
bench/transcript_tasks/ — Transcript-sampled benchmark task assembly.

Extracts judgment tasks from real aesop git history, validates oracles,
and produces a stratified task set for realistic benchmark measurement.
"""
from .transcript_sampler import (
    Task,
    TaskOracle,
    TranscriptSampler,
    extract_task_from_commit,
    sanitize_task,
    validate_oracle,
)

__all__ = [
    "Task",
    "TaskOracle",
    "TranscriptSampler",
    "extract_task_from_commit",
    "sanitize_task",
    "validate_oracle",
]
