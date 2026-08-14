# Granular expiration audiences — a sketch

**Status: sketch, not a plan. Nothing is scheduled or committed to.** Captured
2026-08-13 while `EXPIRATION_NOTICES.md` was fresh, so the next person does not
re-derive the data constraint. Numbers below are measured against the dev clone
on that date.

## The want

Today `expiration_notices` notifies **UNIV + WNA, whole facilities**
(`scheduling/tasks/expiration_notices.py`, `FACILITIES = ('UNIV', 'WNA')`) —
the same audience the manual CLI run always used.

The hypothetical: keep those two whole, and add a **subset of allocation types
within NCAR** — `NSC`, `External Project`, `Paid Services` — while excluding
the rest of NCAR (`Divisional`, `Director Reserve`).

## ⚠️ The data constraint that dictates the design

**`allocation_type` names are not unique across facilities.** Two collide
today:

| allocation_type | facilities |
|---|---|
| `Education` | UNIV, WNA |
| `Small` | UNIV, WNA |

So the audience **cannot** be expressed as two flat lists (facilities × types)
— the cross product is wrong the moment a name collides, and two already do.
It has to be a list of **(facility, types) pairs**. This is the same rule as
the repo's standing "pair rules with names, resolve IDs at runtime" convention,
arrived at from the opposite direction.

For reference, the NCAR shape (active projects):

| panel | allocation_type | active projects |
|---|---|---|
| NCAR Labs | Divisional | 101 |
| NCAR-ARP | **NSC** | 36 |
| External Projects | **External Project** | 16 |
| NCAR Director | Director Reserve | 3 |
| NCAR Labs | **Paid Services** | 1 |

Note `NCAR Labs` hosts both `Divisional` and `Paid Services`, so **panel is not
a usable axis either** — it has to be allocation type within facility.

## Shape of the change

```python
@dataclass(frozen=True)
class AudienceScope:
    facility: str
    allocation_types: Optional[Tuple[str, ...]] = None   # None = every type

AUDIENCE = (
    AudienceScope('UNIV'),                                  # unchanged
    AudienceScope('WNA'),                                   # unchanged
    AudienceScope('NCAR', ('NSC', 'External Project', 'Paid Services')),
)
```

`None` meaning "all types" makes today's behaviour a literal special case of
the new one, so the UNIV/WNA audience provably does not move.

The rung loop becomes `MILESTONES × AUDIENCE`, one query per scope. **No dedup
needed across scopes** — a project has exactly one `allocation_type_id`, hence
exactly one facility, so scopes are disjoint by construction.

## No schema change

The whole `Project → AllocationType → Panel → Facility` chain already exists and
is already walked by today's facility filter (`sam/queries/expirations.py:199-202`).
`allocation_type.allocation_type` is `varchar(20) NOT NULL`. Nothing to add —
which matters, because SAM's schema is vendor-owned, the ORM follows the
database, and Alembic covers only `system_status`, so a column would mean a DBA
ticket.

Edits only:

| File | Change |
|---|---|
| `sam/queries/expirations.py` | additive `allocation_type_names` kwarg |
| `scheduling/tasks/expiration_notices.py` | `AudienceScope` + nested loop |
| `sam/queries/expiration_notices.py` | subject branch, if NCAR wants its own |
| `sam/notify/templates/expiration-NCAR.{txt,html}` | new files — see below |
| tests | two new properties |

⚠️ **One implementation trap.** In `get_all_expiring_allocations` the
`AllocationType → Panel → Facility` joins are added **only** when
`facility_names` is truthy. The new filter needs them too, so that block must
become "join if *either* filter is present" — otherwise passing types alone
silently no-ops. Same failure class as the existing edge where an *empty*
`FACILITIES` tuple means **all** facilities rather than none.

## The real cost is copy, not selection

Selection is roughly a day. The templates are the actual work, and they fail
**silently**.

Resolution is `expiration-{facility}` → `expiration-UNIV` → `expiration`, and
only `-UNIV` and `-WNA` exist. An NCAR project therefore renders
**`expiration-UNIV`**, which tells an NSC or Paid Services PI to *"transfer any
required data to your home institution"* under a 90-day grace period framed for
university users. Wrong letter, no error — the fallback is deliberate.

Two questions that are product, not engineering:

1. **Is one `expiration-NCAR` variant enough**, or do NSC / External Project /
   Paid Services need different language from each other? If the latter, the
   template axis has to grow from facility to (facility, allocation_type) — a
   change to `TemplateRenderer.variants()`, which today knows only facilities.
2. **Does the 90-day grace and data-removal policy apply the same way** to Paid
   Services and External Projects? The current body states it as fact. If it
   differs, this is a different letter, not a copy-edit.

The subject line needs an NCAR branch too; the builder has a single
`if facility_name == FacilityName.WNA` special case today.

## Worth adding on day one: a name-drift guard

Filtering on names is right, but silent if a name changes. Rename
`External Project` and that scope quietly matches nothing — those PIs stop being
notified, the run reports `succeeded`, and nothing signals it. Same failure
class as the `NOTIFY_ENABLED` gap that commit 8 exists for.

```python
known = {t for (t,) in session.query(AllocationType.allocation_type).distinct()}
missing = [t for s in AUDIENCE for t in (s.allocation_types or ()) if t not in known]
if missing:
    raise ValueError(f'unknown allocation types in AUDIENCE: {missing}')
```

Three lines; turns a silent audience shrink into a loud failure.

## Tests it would need

Beyond adapting `test_facilities_are_explicit_not_inherited`:

- **Subset within a facility** — an NCAR/`Divisional` project expiring in-band
  is *not* notified while NCAR/`NSC` is. That is the whole feature.
- **The collision does not leak** — a WNA/`Small` project is not pulled in by a
  UNIV scope that permits `Small`. This is the one a flat-list implementation
  fails.

## Volume

Negligible. On the loaded week measured in `EXPIRATION_NOTICES.md`
(2026-11-23, 212 projects / 824 messages), the NCAR subset adds **7 projects**:
NSC 4, External Project 2, Paid Services 1. The `SAM_TASKS_EMAIL_MAX` cap of
2500 is unaffected.

This is a correctness-and-copy exercise, not a capacity one.

## What does not change

The dedup key (already project + date + rung + recipient), the pre-filter, the
milestone ladder, the last-notified badge, the summary email, and Helm — the
audience stays in code beside `MILESTONES`, where it gets review, rather than
becoming a runtime env knob.
