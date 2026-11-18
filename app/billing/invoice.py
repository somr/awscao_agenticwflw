"""Invoice arithmetic."""


def total(items):
    result = 0
    for item in items:
        result += item["price"] * item["quantity"]
    return result


def discount(amount, percent):
    return amount - amount * percent / 100


def parse_quantity(text):
    return int(text)


def fmt(amt):
    return "%.2f" % amt


def tax(amount, rate):
    """Tax due on amount; rate is a fraction, for example 0.2 for 20 %."""
    return amount * rate
