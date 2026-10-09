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
| `0003-Skip-a-modify-for-an-unknown-key…` | a modify for an unknown key re-downloads SAM every 300 s |

To change one: clone the zoo repo elsewhere (the zoo stays read-only), check out the pin,
`git am` these, edit, run `runtests` in the syncd image, and `git format-patch <pin>` back
here.
