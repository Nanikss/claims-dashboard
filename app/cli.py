"""``flask --app app seed`` - (re)build the SQLite database from the sample generator."""
from __future__ import annotations

import os

import click
from flask import Flask, current_app

from .db import load
from .sample_data import generate


def register(app: Flask) -> None:
    @app.cli.command("seed")
    @click.option("--seed", default=42, show_default=True, help="Random seed.")
    @click.option("--claims", default=2000, show_default=True, help="Number of claims.")
    def seed_command(seed: int, claims: int) -> None:
        """Drop and recreate the database with deterministic sample data."""
        path = current_app.config["DATABASE"]
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        policies, claim_rows = generate(seed=seed, claims=claims)
        load(path, policies, claim_rows)
        click.echo(f"Seeded {len(policies)} policies and {len(claim_rows)} claims into {path}")
