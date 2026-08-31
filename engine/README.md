# Execution engine

Runtime implementation: `src/atmpl/engine/`. It persists ordered runs and stages in SQLite,
attaches AI output as draft-only evidence, requires typed human decisions for MEDIUM/HIGH,
and emits an audit event for every transition.

