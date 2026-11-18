"""Shopping cart totals."""
from app.billing.invoice import discount, fmt, tax, total

TAX_RATE_PERCENT = 20


def checkout(items, discount_percent):
    subtotal = discount(total(items), discount_percent)
    return fmt(subtotal + tax(subtotal, TAX_RATE_PERCENT))
