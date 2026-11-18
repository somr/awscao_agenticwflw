"""Shopping cart totals."""
from app.billing.invoice import discount, format_amount, tax, total

TAX_RATE = 0.20


def checkout(items, discount_percent):
    subtotal = discount(total(items), discount_percent)
    return format_amount(subtotal + tax(subtotal, TAX_RATE))
