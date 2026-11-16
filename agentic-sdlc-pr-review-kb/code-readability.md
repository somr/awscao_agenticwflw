# Code readability rules

## NAMING-1 - [MANDATORY] Readability. Identifiers are full, self-explanatory names

### Applicable files

- `app/**/*.py`
- `app/**/*.js`
- `app/**/*.scala`

### Rule description

Names of variables, parameters, functions, methods, classes, constants and modules use whole words that say what the
thing is or does. They are not acronyms, abbreviations or single letters that the reader has to decode.

A name may contain a term that is better known in its short form than spelled out, such as `id`, `url`, `http`,
`json`, `sql`, `uuid` or `iban`. A loop index (`index`, or `i` in a loop of at most three lines) is also allowed. Any
other short form is a violation, including ones that are common inside the team (`amt`, `txn`, `cust`, `cfg`,
`mgr`).

The rule applies to names that the change adds or renames. Existing names that the change only uses are not findings.

### Why we have the rule

Code is read far more often than it is written, often by someone who did not write it or by an agent that has none
of the author's context. A full name explains itself at every place it is used. An acronym needs the reader to find
its definition, guess it, or ask. A wrong guess (`pmt` as payment or as payment method) leads to defects that look
like correct code.

### Examples: bad

```python
def calc_fee(amt, cur, txn_typ):
    r = get_rt(cur)
    fa = amt * r
    if txn_typ == "INTL":
        fa += INTL_SURCH
    return fa
```

```python
class PmtMgr:
    def proc(self, req):
        ...
```

### Examples: good

```python
def calculate_fee(amount, currency, transaction_type):
    exchange_rate = get_exchange_rate(currency)
    fee = amount * exchange_rate
    if transaction_type == "INTERNATIONAL":
        fee += INTERNATIONAL_SURCHARGE
    return fee
```

```python
class PaymentProcessor:
    def process(self, payment_request):
        ...
```

```python
def find_customer_by_id(customer_id):
    ...
```

## COMMENTS-1 - [MANDATORY] Maintainability. No change-history comments in the code

### Applicable files

- `app/**/*.py`
- `app/**/*.js`
- `app/**/*.scala`

### Rule description

Comments describe the code as it is now: what it does when that is not obvious, and why it is written this way. They
never record how the code changed over time. That includes:

- dates of changes, removals or additions (`Rule removed on 20-Jan`, `added 2024-03-02`);
- authors or tickets tied to a change (`changed by JS`, `fixed for PAY-123`);
- version markers (`v2: added retry`, `new logic`, `old behaviour`);
- old code kept as a comment "for reference".

The rule applies to comments that the change adds or edits.

### Why we have the rule

Version control already records every change, with its author, date and reason, and keeps that record accurate.
History comments repeat it badly: they are never updated, they pile up, and after a few changes they describe code
that no longer exists. The reader has to work out which part of the comment is still true, and old code left as a
comment is easy to mistake for live code.

### Examples: bad

```python
# Rule removed on 20-Jan
def validate_payment(payment):
    # Changed by JS 2024-03-02: limit was 5000
    if payment.amount > 10000:
        raise PaymentLimitExceeded(payment.amount)
    # v2: added currency check
    if payment.currency not in SUPPORTED_CURRENCIES:
        raise UnsupportedCurrency(payment.currency)
```

```python
def refund(payment):
    # Old version, kept for reference:
    # return gateway.reverse(payment.id)
    return gateway.refund(payment.id, payment.amount)
```

### Examples: good

```python
def validate_payment(payment):
    # The card network rejects single payments above this amount, so fail before calling it.
    if payment.amount > 10000:
        raise PaymentLimitExceeded(payment.amount)
    if payment.currency not in SUPPORTED_CURRENCIES:
        raise UnsupportedCurrency(payment.currency)
```

```python
def refund(payment):
    # A partial refund needs the amount; the gateway's reverse call only undoes whole payments.
    return gateway.refund(payment.id, payment.amount)
```

The reason for a change goes in the commit message or the pull request, not in the code.
