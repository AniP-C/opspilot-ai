"""Phase 2 evaluation framework (TEST / OFFLINE ONLY).

This package reads the hidden ``ground_truth`` from the synthetic dataset to
*measure* investigation quality. It is imported only by tests and the evaluation
runner — never by the investigation engine, graph, or any runtime path — so
ground truth can never leak into reasoning.
"""
