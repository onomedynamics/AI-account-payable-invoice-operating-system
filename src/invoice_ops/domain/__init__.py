"""Domain layer: invoice lifecycle state machine and deterministic rules.

Deliberately empty in M0. The validation engine (M4), approval policy and
state machine (M5) live here -- pure functions, no I/O, unit-tested in
isolation.
"""
