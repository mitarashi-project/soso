"""Create minimal, shareable usage status; never reads credentials or calls APIs."""
import argparse
from datetime import datetime, timezone, timedelta
import json
import math
from pathlib import Path


def status(agent, remaining=None, reset_at=None, observed_at=None, now=None):
    now = now or datetime.now(timezone.utc)
    if remaining is not None and (not math.isfinite(remaining) or not 0 <= remaining <= 100):
        raise ValueError('remaining must be 0..100')
    def parse(value):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Timestamp must include timezone')
        return parsed
    if remaining is not None and observed_at is None:
        raise ValueError('Known usage requires an observation timestamp')
    observed = parse(observed_at) if observed_at else None
    reset = parse(reset_at) if reset_at else None
    if observed and observed > now:
        raise ValueError('Observation cannot be in the future')
    stale = observed is None or now - observed > timedelta(hours=6) or (reset is not None and reset <= now)
    admission = 'unknown' if remaining is None or stale else ('paused' if remaining <= 20 else ('light_only' if remaining <= 40 else 'available'))
    return {'schema_version': 1, 'agent': agent, 'remaining_percent': remaining,
            'reset_at': reset_at, 'observed_at': observed_at, 'updated_at': now.isoformat(),
            'stale': bool(stale), 'accepting_work': admission}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--agent', required=True)
    p.add_argument('--remaining', type=float)
    p.add_argument('--reset-at')
    p.add_argument('--observed-at')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    try:
        record = status(a.agent, a.remaining, a.reset_at, a.observed_at)
    except ValueError as exc:
        p.error(str(exc))
    path = Path(a.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
