#!/usr/bin/env python3
"""Standalone helper script to populate the Lainamääkärit SQLite database idempotently."""

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lainamaakarit.settings")
    import django

    django.setup()
    from django.core.management import call_command

    call_command("populate_db")


if __name__ == "__main__":
    main()
