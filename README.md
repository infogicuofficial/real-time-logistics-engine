# Hearthline — real-time restaurant logistics

A role-aware restaurant operations system connecting dining-room service, kitchen production, attendance, and payment. It is intentionally designed as an operational interface—not a generic analytics template.

## Included

- Custom `User` with Admin, Manager, Waiter, and Chef roles
- Normalized menu catalogue with size/quantity variations and private margin data
- Live waiter service page with item timelines and AJAX updates
- Concurrency-safe kitchen routing: primary chef → alternate chef → least-loaded eligible chef
- Strict 10-active-item queue ceiling and capacity-aware transfers
- Kitchen Kanban with validated state transitions and bulk-ready action
- Cash, card, bank, and mobile checkout with server-side validation
- 80mm thermal receipt print stylesheet
- Attendance check-in and enforced 2× late-minute penalty after the 09:30 grace boundary
- Browser-side 300×300 image crop/compression before upload
- PostgreSQL-ready environment configuration; SQLite fallback for local evaluation

## Quick start

Python 3.11 is recommended.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 0.0.0.0:8000
```

Demo password is `demo` for each role-oriented account:

| Account | Experience |
|---|---|
| `admin` | Admin analytics and Django administration |
| `manager` | Operations analytics and menu management |
| `waiter` | Dining room, ordering, tracking, and checkout |
| `chef` | Capacity-controlled kitchen display |

The seed command is idempotent and creates tables, chefs, menu items, and one active service.

## PostgreSQL 16 configuration

Set these variables before migration. If `POSTGRES_DB` is absent, development uses SQLite.

```bash
export POSTGRES_DB=hearthline
export POSTGRES_USER=hearthline
export POSTGRES_PASSWORD='replace-me'
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export DJANGO_SECRET_KEY='a-long-random-production-secret'
export DJANGO_DEBUG=0
export DJANGO_ALLOWED_HOSTS='restaurant.example.com'
export DJANGO_CSRF_TRUSTED_ORIGINS='https://restaurant.example.com'
export TIME_ZONE='Europe/Rome'  # use the restaurant’s actual zone
python manage.py migrate
```

Do not store these values in Git. Use a deployment secret manager or a local `.env` ignored by Git.

## Redis / Channels rollout (deliberately not enabled yet)

The current UI uses focused AJAX updates and 4-second waiter polling, so the application can be evaluated without infrastructure. Redis should be introduced as a distinct deployment phase rather than silently making local setup fail.

When the deployment environment is ready, the safe sequence is:

1. Install Redis 7.2 with authentication and private-network binding.
2. Add `channels[daphne]` and `channels-redis` to requirements.
3. Set `ASGI_APPLICATION` and a Redis-backed `CHANNEL_LAYERS` configuration via `REDIS_URL`.
4. Add order-specific and chef-specific consumers with authenticated group membership.
5. Emit events only from `transaction.on_commit(...)`, preventing clients from observing rolled-back database work.
6. Run Daphne/Uvicorn as ASGI and keep PostgreSQL as the source of truth; Redis carries notifications, never order state.
7. Add reconnect/backoff behavior and retain polling as graceful degradation.

This avoids race conditions between database commits and WebSocket broadcasts. The routing service already uses PostgreSQL row locks and can be connected to Channels without redesigning domain models.

## Business rules

### Kitchen capacity

`logistics/services/routing.py` locks active chef rows in one database transaction, counts queue items in `PLACED`, `ACCEPTED`, or `PREPARING`, and selects:

1. primary chef if below 10;
2. alternate chef if below 10;
3. globally least-loaded active chef below 10;
4. otherwise rejects placement with a clear “kitchen at capacity” error.

`READY` immediately frees capacity. Every status change is preserved in `OrderItemEvent`.

### Attendance

The standard start is 09:00 and grace ends at 09:30. Only full minutes after grace count:

```text
late_minutes = max(0, check_in - 09:30)
penalty_minutes = late_minutes × 2
required_minutes = expected_shift_minutes + penalty_minutes
```

Checkout is rejected by the backend until worked minutes meet required minutes; the disabled button is only an additional UI cue.

### Payment security

The database never stores a full card number or CVV. It records only the provider reference and optional last four digits. Production card collection should use hosted/tokenized fields from a PCI-compliant gateway.

## Verification

```bash
python manage.py check
python manage.py test
```

The tests cover chef fallback/capacity behavior and attendance penalty math.
