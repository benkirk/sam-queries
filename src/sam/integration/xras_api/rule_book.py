"""XRAS's rule book for one allocation type x action type: the fields and documents it requires.

Two reads, memoised a day in the lookups bucket: ``required_fields`` and ``/types/documents``.
When XRAS cannot answer, ``RULE_BOOK_FALLBACK`` (measured 2026-10-27, sprint doc § 9) stands
in by allocation-type name, so a form still asks for what validate will want. XRAS's own
validate on submit stays the gate. Flask-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sam.integration.xras_api.base import XrasSourceUnavailable

#: By XRAS allocation-type name x action type: (required request fields, documents as (code,
#: label, hard page limit)). New, documents and the Data Analysis / External fields were read
#: 2026-10-27; grants on a Small or Large Renewal are the design's (§ 4.1), not yet read.
RULE_BOOK_FALLBACK: Dict[Tuple[str, str], Tuple[Tuple[str, ...], Tuple[Tuple[str, str, Optional[int]], ...]]] = {
    ('Exploratory', 'Renewal'): (('title', 'abstract'), (('Advisor_Letter', 'Advisor Letter', None),)),
    ('Small', 'Renewal'): (('title', 'abstract', 'grants'), (('Progress_Report', 'Progress Report', None),)),
    ('Large', 'Renewal'): (('title', 'abstract', 'grants'), (('Main_Doc', 'Main Document', 10),)),
    ('NCAR Strategic Computing', 'Renewal'): (('title', 'abstract'), (('Main_Doc', 'Main Document', 10),)),
    ('Educational', 'Renewal'): (('title', 'abstract'), (('Progress_Report', 'Progress Report', None),
                                                         ('Supp_Info', 'Supporting Information', None))),
    ('Data Analysis', 'Renewal'): (('title', 'abstract'), ()),
    ('NCAR External Projects', 'Renewal'): (('title', 'abstract'), ()),
    ('Exploratory', 'New'): (('title', 'abstract'), (('Advisor_Letter', 'Advisor Letter', None),)),
    ('Small', 'New'): (('title', 'abstract', 'grants'), ()),
    ('Large', 'New'): (('title', 'abstract', 'grants'), (('Main_Doc', 'Main Document', 10),)),
    ('NCAR Strategic Computing', 'New'): (('title', 'abstract'), (('Main_Doc', 'Main Document', 10),)),
    ('Educational', 'New'): (('title', 'abstract'), (('Supp_Info', 'Supporting Information', None),)),
    ('Data Analysis', 'New'): (('title', 'abstract'), ()),
    ('NCAR External Projects', 'New'): (('title', 'abstract', 'grants'), ()),
}

#: The grant fields validate demands beyond the number, by allocation-type name (read
#: 2026-10-27: External asks no officer and no title). Wire names, as ``add_grant`` sends them.
GRANT_FIELDS = ('title', 'piName', 'beginDate', 'endDate', 'programOfficerName',
                'programOfficerEmail')
GRANT_FIELDS_BY_TYPE = {'NCAR External Projects': ('piName', 'beginDate', 'endDate')}


@dataclass(frozen=True)
class DocumentRule:
    code: str
    label: str
    required: bool = True
    hard_page_limit: Optional[int] = None
    allow_many: bool = False


@dataclass(frozen=True)
class RuleBook:
    required: Tuple[str, ...] = ()          # request-level field names XRAS requires
    documents: Tuple[DocumentRule, ...] = ()
    source: str = 'xras'                    # xras | fallback | none
    grant_fields: Tuple[str, ...] = GRANT_FIELDS

    @property
    def wants_grants(self) -> bool:
        return 'grants' in self.required


def _required_names(required_fields: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    request = ((required_fields or {}).get('requiredFields') or {}).get('request') or {}
    return tuple(sorted(name for name, rule in request.items()
                        if isinstance(rule, dict) and rule.get('required') is True))


def _document_rules(document_types: Optional[List[Dict[str, Any]]], allocation_type_id: int,
                    action_type: str) -> Tuple[DocumentRule, ...]:
    out = []
    for doc in document_types or ():
        if not isinstance(doc, dict) or doc.get('isActive') is False:
            continue
        for binding in doc.get('allocationActionTypes') or ():
            if binding.get('allocationTypeId') != allocation_type_id:
                continue
            for rule in binding.get('actionTypes') or ():
                if rule.get('actionType') == action_type:
                    out.append(DocumentRule(
                        doc.get('documentType'), doc.get('displayDocumentType')
                        or doc.get('documentType'), bool(rule.get('isRequired')),
                        rule.get('hardPageLimit'), bool(rule.get('allowMany'))))
    return tuple(out)


def _grant_fields(allocation_type_name: Optional[str]) -> Tuple[str, ...]:
    return GRANT_FIELDS_BY_TYPE.get(allocation_type_name or '', GRANT_FIELDS)


def _fallback(allocation_type_name: Optional[str], action_type: str) -> RuleBook:
    found = RULE_BOOK_FALLBACK.get((allocation_type_name or '', action_type))
    if found is None:
        return RuleBook(source='none')
    required, documents = found
    return RuleBook(required, tuple(DocumentRule(c, label, True, limit)
                                    for c, label, limit in documents), source='fallback',
                    grant_fields=_grant_fields(allocation_type_name))


def rule_book(reader, allocation_type_id: Optional[int], action_type: str, *,
              allocation_type_name: Optional[str] = None) -> RuleBook:
    """The rule book for *allocation_type_id* x *action_type*; the fallback when XRAS cannot answer."""
    from sam.integration.xras_api.cache import cached_document_types, cached_required_fields
    if allocation_type_id is None:
        return _fallback(allocation_type_name, action_type)
    try:
        fields = cached_required_fields(
            allocation_type_id, action_type,
            lambda: reader.required_fields(allocation_type_id, action_type))
        documents = cached_document_types(reader.get_document_types)
    except XrasSourceUnavailable:
        return _fallback(allocation_type_name, action_type)
    if fields is None:
        return _fallback(allocation_type_name, action_type)
    return RuleBook(_required_names(fields),
                    _document_rules(documents, int(allocation_type_id), action_type),
                    grant_fields=_grant_fields(allocation_type_name))
