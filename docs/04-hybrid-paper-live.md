# Hybrid Paper + Live

Every accepted decision is paper-traded. Live is a **clone** of the same ticket, not a different model.

```
signal → decide() → paper OrderManager
                       └─ if live gate and adapter → live OrderManager
```

## Books

Two `account_state` rows: `paper` and `live`. Two equity curves. They never share cash.

## Brokers

- `PaperBroker` — always on, in-process, starts at ₹50,000.
- `PluginBroker` — empty slot. Register a real adapter with `register_broker(...)`.
- No Kite / SmartAPI / SmartConnect code is bundled. That is how the desk stays broker-agnostic.

## Comparison

Command shows both books. `/api/equity` returns both curves. The point is to see whether the live subset (the rare high-confidence clips) is better or worse than “trade everything on paper”.

## Arm switch

Live is **off** on a fresh seed. `python -m meridian_v3 arm --on` or the Safety page. Disarm is one click.
