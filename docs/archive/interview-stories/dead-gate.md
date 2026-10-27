# The Dead Gate

*A fail-closed gate that was always failing closed -- and nobody noticed.*

## Context

Aesop is a multi-agent orchestration harness where fleets of LLM coding agents
work in parallel on disjoint file lanes. When multiple agents might try to claim
the same resource (a lane, a wave slot), the system uses a coordination layer
built on top of an append-only event store. The primitive is `try_claim`: append
a `claim_requested` event to a shared stream, re-read the stream, fold it to
see who won, and return True or False. Crucially, the contract is **fail-closed**:
if anything goes wrong -- append fails, read fails, fold throws -- the function
returns False. You never accidentally hold a claim you didn't earn.

This is exactly the right design. A coordination primitive that fails *open*
would let two agents stomp on each other's files, producing merge conflicts or
silent data corruption. Fail-closed means the worst case is wasted work (an
agent that could have claimed a lane doesn't), not incorrect work.

Or so we thought.

## The Bug

The `try_claim` function lived in `state_store/coordination.py`. It accepted a
`store` parameter and called `store.get("claims")` to read back the claims
stream after appending. But the wave loop in `driver/wave_loop.py` -- the
actual caller in production -- passed a raw `EventStore` instance, not a
`StateAPI` wrapper. The `EventStore` exposes `read()`, not `get()`.

```python
# What try_claim did (before the fix):
def try_claim(store, resource, instance_id, ttl=300.0):
    try:
        store.append("claims", "claim_requested", {...})
        events = store.get("claims")  # <-- AttributeError on EventStore
        claims = fold_claims(events)
        return claims.get(resource) == instance_id
    except Exception:
        return False  # Fail-closed: any exception = claim not held
```

`EventStore` has no `.get()` method. Every call to `try_claim` raised
`AttributeError`. The `except Exception` caught it. The function returned
`False`. Every time. For every resource. For every agent.

The gate was correct -- it failed closed. But it was also dead. No agent
ever successfully claimed a lane through this path. Instead, items silently
fell through to the claim-less dispatch fallback, where the wave loop
assigned work without coordination.

The system kept working because single-instance Aesop doesn't actually *need*
claims -- there's only one orchestrator. But the entire coordination layer, the
one we'd need the moment a second instance appears, was inert. We'd
carefully built a lock, tested it with unit tests that passed a `StateAPI`
(which *does* have `.get()`), and shipped it. It worked in tests. It was dead
in production.

## Discovery

This was not found by code review. The code was reviewed multiple times. The
fail-closed pattern looked correct -- it *is* correct. The type mismatch
between `StateAPI.get()` and `EventStore.read()` was invisible unless you
traced the actual call path from `wave_loop.py` through to `coordination.py`
and noticed the caller passed a different type than the tests did.

It was found by **behavioral proof**: an adversarial audit wave that didn't
just inspect the code but asked "has this gate ever returned True in
production?" The answer was no. Not once. The audit tagged it RS3-W (Review
Sprint 3, Wave finding) and it was fixed the same day.

## The Fix

The fix was a three-line duck-typing adapter:

```python
def _read_claim_events(store) -> list:
    """Read claims stream from StateAPI (.get) OR EventStore (.read)."""
    getter = getattr(store, "get", None)
    if callable(getter):
        return getter("claims")
    return store.read("claims")
```

Every internal call site that read from the claims stream was replaced with
`_read_claim_events(store)`. The function accepts either API surface -- if
`.get()` exists and is callable, use it; otherwise fall back to `.read()`.

But the deeper fix was the realization that unit tests had created a false
sense of safety. The tests passed a `StateAPI` and verified correct claim
semantics. Those semantics *were* correct. But the production caller used a
different type, and the fail-closed contract meant the mismatch was silent.

## Design Lesson

The dead gate pattern is insidious specifically because it afflicts
*well-designed* systems. A fail-open gate that malfunctions is noisy -- it
lets bad things through, and those bad things are visible. A fail-closed gate
that malfunctions is silent -- it blocks everything, and blocking is what a
careful system already does in edge cases. The gate looks like it's doing its
job. It's "being conservative." In reality, it has never once been alive.

The root cause is that **fail-closed correctness and liveness are orthogonal
properties**. A gate can be correct (it never grants a false claim) and dead
(it never grants a true claim). Testing correctness alone -- "does it reject
bad inputs?" -- doesn't reveal deadness.

> **Design Principle**
>
> Every fail-closed gate needs two signals, not one:
>
> 1. **Correctness**: does it reject invalid states? (unit test)
> 2. **Liveness**: has it ever *accepted* a valid state? (behavioral proof)
>
> If the answer to (2) is "no" after any meaningful production run, the gate
> is dead. Instrument it. A gate that has never passed is indistinguishable
> from a gate that is broken. In aesop, this became a standing audit question:
> for every fail-closed gate, show me the last time it returned the
> non-default outcome.
