"""Backbone adapters and fusion heads built on third-party RF models.

Every architecture here is our own reimplementation, written from the model
cards, published code read as text, and the checkpoint tensor shapes. No
third-party code is imported or executed, and weights are only read from
safetensors files or from plain-tensor torch checkpoints loaded with
``weights_only=True`` (see ``loading.py``).
"""
