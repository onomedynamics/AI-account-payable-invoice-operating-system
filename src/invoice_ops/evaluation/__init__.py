"""Offline scoring for the extraction eval harness.

Pure functions only: compare an extracted invoice against a ground-truth
invoice and produce per-field verdicts + an aggregate. The runner that feeds
real fixtures through a real model lives in ``evals/run.py`` at the repo root.
"""
