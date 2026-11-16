"""Errors safe to show without exposing provider responses or credentials."""


class ConchError(Exception):
    """An actionable configuration, authentication, or provider failure."""
