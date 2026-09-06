"""Semantic knowledge layer (Chroma).

Responsible for ingesting and retrieving historical incidents and runbooks.
Embeddings default to a deterministic, offline hashing function so retrieval
needs no network access, no model downloads and no credentials.
"""
