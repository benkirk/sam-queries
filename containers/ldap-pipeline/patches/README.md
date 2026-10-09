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

The patches apply independently but 0002 assumes 0001: its restore puts back the IMDB's
pre-pass record, which equals SAM only when the IMDB was rebuilt on the object the loop
reads. A record 0002 skips is re-sent by the next full dump (the daily snapshot or a
reset); a skipped **modify** is not retried before that unless the record changes again.

To change one: `git archive` the pin from the zoo repo into a scratch directory (the zoo
stays read-only), `git init`, `git am` these, edit, run the `.t` files with the image's
Perl (`docker run --rm --entrypoint /usr/local/bin/perl -v <scratch>:/work -w /work/lib
-e PERL5LIB=/work/lib:/usr/local/sweet-pl5/lib:/usr/local/ucarldap/scripts
ghcr.io/ncar/sam-ldap-syncd:pipeline-local <file>.t`; the image's `prove` belongs to
another Perl), and `git format-patch <base>` back here.
