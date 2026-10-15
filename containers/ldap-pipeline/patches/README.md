# syncd patches

Fixes we carry for `sam-ldap-syncd` until they are filed upstream. Each is one idea, made
with `git format-patch` against the prod pin in `../pins`, and applies to upstream HEAD as
well. They are a deliberate departure from "prod, bugs included": the default build ignores
them, and `PATCHED=1 bin/build-images sam-ldap-syncd` builds `:<tag>-patched` with them.
Run that image with `SYNCD_TAG=<tag>-patched` in `.env`.

| Patch | Fixes (`docs/plans/SAM_LDAP_SYNCD_REFERENCE.md`) |
|---|---|
| `0001-Reuse-the-IMDB-object…` | N5: an in-loop rebuild from SAM leaves the loop on stale data |
| `0002-Restore-and-skip-a-record…` | 15: one rejected PUT loses that record and, in a full dump, the rest of the pass |
| `0003-Skip-a-modify-for-an-unknown-key…` | N23: a modify for an unknown key re-downloads SAM every 300 s |
| `0004-Truncate-an-institution-acronym…` | N22: an acronym longer than SAM's 40 characters is rejected on every pass |
| `0005-Retry-every-transport-failure…` | a timeout, DNS or TLS failure is LWP's own 500; it was thrown (and under 0002 skipped) instead of retried like a refused connection |
| `0006-Truncate-a-tombstone-acronym…` | `delete()` cut the acronym with an unset variable, so a tombstone of a long acronym was `" --x"` (65 institutions, 22 organizations qualify today) |
| `0007-Keep-a-record-SAM-rejects…` | 0002's restore lost the rest of a rejected update: a later update undoing only the rejected field was a no-op, so a reactivation sent with a transient bad username never reached SAM (2026-10-14). A rejected record now stays in the IMDB, owed (`sam-owed.json`), and is re-sent whole by the next update to it, no-op included, until SAM takes it |

The patches apply independently, except 0007, which builds on 0002; 0002 assumes 0001: its restore puts back the IMDB's
pre-pass record, which equals SAM only when the IMDB was rebuilt on the object the loop
reads. Under 0007 a rejected record is not restored: it stays as IdM has it, marked owed in
`sam-owed.json` beside `sam-data.json`, and is re-sent by the next update that reaches it (a
change, or the no-op of the next full dump) until SAM takes it. A rejected delete is still
restored and re-sent by the next full dump.

To change one: `git archive` the pin from the zoo repo into a scratch directory (the zoo
stays read-only), `git init`, `git am` these, edit, run the `.t` files with the image's
Perl (`docker run --rm --entrypoint /usr/local/bin/perl -v <scratch>:/work -w /work/lib
-e PERL5LIB=/work/lib:/usr/local/sweet-pl5/lib:/usr/local/ucarldap/scripts
ghcr.io/ncar/sam-ldap-syncd:pipeline-local <file>.t`; the image's `prove` belongs to
another Perl), and `git format-patch <base>` back here.
