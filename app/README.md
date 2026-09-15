# payment_service

Run the test suite from the repository root:

```bash
python3 -m unittest discover -t app -s app/tests -v
```

`-t app` puts `app/` on `sys.path` so `payment_service` resolves as a top-level package; `-s app/tests` is where discovery starts.
