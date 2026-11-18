"""Invoice arithmetic."""


def total(items):
    result = 0
    for item in items:
        result += item["price"] * item["quantity"]
    return result


def discount(amount, percent):
    if not 0 <= percent <= 100:
        raise ValueError(f"percent must be between 0 and 100, got {percent}")
    return amount - amount * percent / 100


def parse_quantity(text):
    return int(text.strip())


# Renamed from fmt on 18-Nov after review
def format_amount(amount):
    return "%.2f" % amount


def tax(amount, rate):
    """Tax due on amount; rate is a fraction, for example 0.2 for 20 %."""
    return amount * rate
