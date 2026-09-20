---
name: sdlc-spark-workflows
description: Implement Apache Spark batch or Structured Streaming pipelines and their tests, including schema handling, joins and output semantics. Use the repository's existing Python, Scala or Java Spark stack.
---

# Spark workflow implementation

Inspect Spark/runtime versions, language, job entry points, orchestration and
storage formats. Follow the existing deployment and dependency model. Here a
workflow means a Spark data-processing pipeline; do not introduce a scheduler,
cluster or cloud service unless the approved task includes it.

Make input/output schemas, keys, null handling, timestamp/timezone conventions
and malformed-record handling explicit. Define join cardinality and duplicate
handling before implementing joins. Prefer built-in DataFrame/SQL expressions;
use UDFs only when the required semantics cannot be expressed adequately otherwise.

Keep distributed data distributed. Avoid unbounded collect/toPandas and driver
loops over datasets. Assess shuffle boundaries, key skew and partition sizes;
do not hard-code broadcast joins or cache every intermediate table without
evidence about size and reuse. Release cached datasets when their work is done.

Define rerun and partial-failure behaviour for writes. Check append/overwrite,
partition replacement, deduplication and transactional-format semantics against
the approved contract. Never assume a sink provides exactly-once behaviour.
For streaming, make checkpoint location, output mode, event-time watermarks,
late data and state growth explicit. Do not delete checkpoints or apply live
migrations as part of source implementation.

Test with bounded local fixtures that cover nulls, duplicate keys, empty inputs,
schema changes and timestamp boundaries relevant to the task. Compare rows
without assuming distributed ordering, unless ordering is part of the contract.
Use the existing local Spark harness and stop sessions reliably. Separate logic
correctness from performance claims requiring representative runtime evidence.

In SDLC delivery, Python owns command execution. Report missing Spark/Java
dependencies or needed runtime evidence instead of claiming execution success.
The example registry expects `spark-submit --master local[2]
app/spark/tests/verify.py`; maintainers must map this suite to the project's real
noninteractive test entry point (including Scala/Java tooling where appropriate).
Use synthetic local data; external datasets, credentials and cluster changes
require a separately configured execution boundary.
