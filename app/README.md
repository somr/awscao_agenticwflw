# Payment-service fixture

This small application is the repository's delivery-workflow example. It provides a
payment service, callback controller, fulfilment service and repository abstraction so
the approved-plan, implementation, verification and review stages have real Python
source to inspect. The repository uses an in-memory/SQLite-style stand-in rather than
an external payment provider.

Run the test suite from the repository root:

```bash
python3 -m unittest discover -t app -s app/tests -v
```

`-t app` puts `app/` on `sys.path` so `payment_service` resolves as a top-level package; `-s app/tests` is where discovery starts.
