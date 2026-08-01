"""Backward-compatible import for the transaction-safe number generator."""

from datetime import date

from services.transaction_workflows import generate_transaction_number as _generate


def generate_transaction_number(cursor, transaction_type, transaction_date=None):
    return _generate(cursor, transaction_type, transaction_date or date.today())
