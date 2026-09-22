"""Clients for upstream APIs that more than one tool talks to.

A client used by exactly one tool belongs in that tool's package; it moves here
when a second tool needs it. Nothing in here knows about scheduling or about any
particular job — it is just the API surface.
"""
