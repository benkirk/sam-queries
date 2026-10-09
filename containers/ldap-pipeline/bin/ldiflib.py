"""LDIF reading and writing shared by ldif-fold and ldap-poll. Python 3.7 (the ldap image)."""
import base64

BASE = "dc=ucar,dc=edu"
# The seven subtrees the transformer reads (sam-idms-ldap's entrypoint help lists them).
SUBTREES = ["ou=allPeople", "ou=allOrganizations", "ou=externalOrgs", "ou=allGroups",
            "ou=groups,ou=unix", "ou=serviceAccounts", "ou=gidAllocations"]
# Readable as citldapsam, not anonymously (measured 2026-10-08, folded seed vs ldap.ucar.edu).
HIDDEN = {"x-ucar-contactperson", "x-ucar-peid", "x-ucar-office", "telephonenumber"}


def norm(dn):
    return ",".join(p.strip() for p in dn.lower().split(","))


def records(lines):
    """Yield each LDIF record as its logical (unwrapped) lines, comments dropped."""
    rec = []
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith(" ") and rec:
            rec[-1] += line[1:]
        elif line == "":
            if rec:
                yield rec
            rec = []
        elif not line.startswith("#"):
            rec.append(line)
    if rec:
        yield rec


def split(line):
    """'attr: v' / 'attr:: b64' -> (attr, raw bytes)."""
    attr, rest = line.split(":", 1)
    if rest.startswith(":"):
        return attr, base64.b64decode(rest[1:].strip())
    return attr, rest.strip().encode("utf-8", "surrogateescape")


def render(attr, value):
    try:
        text = value.decode("utf-8")
        safe = text == text.strip() and not text.startswith((":", "<")) and \
            all(32 <= ord(c) < 127 for c in text)
    except UnicodeDecodeError:
        safe = False
    return "%s: %s" % (attr, text) if safe else \
        "%s:: %s" % (attr, base64.b64encode(value).decode())


class Entry:
    def __init__(self, dn):
        self.dn, self.attrs = dn, {}          # lower attr -> [name, [values]]

    @classmethod
    def parse(cls, rec):
        e = cls(split(rec[0])[1].decode("utf-8", "surrogateescape"))
        for line in rec[1:]:
            e.add(*split(line))
        return e

    def get(self, attr):
        slot = self.attrs.get(attr.lower())
        return slot[1] if slot else []

    def first(self, attr):
        values = self.get(attr)
        return values[0].decode("utf-8", "surrogateescape") if values else None

    def add(self, attr, value):
        slot = self.attrs.setdefault(attr.lower(), [attr, []])
        if value not in slot[1]:
            slot[1].append(value)

    def delete(self, attr, values=None):
        slot = self.attrs.get(attr.lower())
        if slot is None:
            return
        if values:
            slot[1] = [v for v in slot[1] if v not in values]
        if not values or not slot[1]:
            del self.attrs[attr.lower()]

    def lines(self, skip=()):
        out = [render("dn", self.dn.encode())]
        for key, (name, values) in self.attrs.items():
            if key not in skip:
                out += [render(name, v) for v in values]
        return out
