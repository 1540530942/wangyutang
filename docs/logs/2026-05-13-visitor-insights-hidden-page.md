# 2026-05-13 Visitor Insights Hidden Page

## Goal

Add a hidden page for visitor registration records and behavior habits.

## Privacy Boundary

The implementation is intentionally limited:

- Tracks only the hidden page itself.
- Requires visitor consent before storing registration or behavior events.
- Does not do cross-site tracking.
- Does not create browser fingerprints.
- Does not collect passwords or precise location.

## Route

```text
/visitor-insights-2026
```

This route is not linked from the public module navigation.

## APIs

```text
POST /api/visitor/register
POST /api/visitor/event
GET  /api/visitor/summary
```

`GET /api/visitor/summary` requires header:

```text
X-Visitor-Admin-Code: 123
```

The admin code can be changed by setting:

```text
VISITOR_ADMIN_CODE
```

## Stored Data

Registration records are stored as JSONL:

```text
control_platform/data/visitor_registrations.jsonl
```

Behavior events are stored as JSONL:

```text
control_platform/data/visitor_events.jsonl
```

Recorded fields include:

- visitor name or display name
- contact
- visit purpose
- note
- session id
- page
- event type
- click target
- duration seconds
- coarse client IP from request headers
- user agent
- referer
- created timestamp

## Verification

Local verification:

- hidden page returns HTTP 200
- registration without consent returns HTTP 400
- registration with consent returns HTTP 200
- event upload with consent stores data
- wrong admin code returns HTTP 401
- admin code `123` returns summary

Tencent Cloud verification:

- `https://www.wangyutang.cn/visitor-insights-2026` returns the hidden page
- `/api/visitor/register` accepts consented registration
- `/api/visitor/event` accepts consented event
- `/api/visitor/summary` rejects wrong code
- `/api/visitor/summary` accepts code `123`
- `control-platform` container is healthy
