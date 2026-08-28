"""The XRAS 422 vocabulary is a wire contract — these tests are the lock on it.

XRAS administrators read these strings in their "Accounting Service Posts" panel and
act on them. Legacy has emitted them for years. So every assertion here is on the
**exact bytes**, including the trailing colon-space, the trailing bare space and the
double space that are typos in the Java we reproduce on purpose.

If one of these fails, the question is never "is the new string nicer" — it is
"did someone tidy a contract". See ``src/sam/xras/errors.py`` for the emitters at
``file:line``, and ``docs/xras/incoming/implemented/XRAS_SPRINT_C.md`` § *The error vocabulary* for the
verified table (WARNING: **not** ``XRAS_REIMPLEMENTATION.md`` § 3.4, which is wrong in
seven places).
"""

import pytest

from sam.xras import errors as e
from sam.xras.errors import ActionErrors, XrasActionRejected

pytestmark = pytest.mark.unit


class TestPunctuationQuirks:
    """The four strings a well-meaning cleanup would silently break."""

    def test_pi_not_active_keeps_its_dangling_colon_space(self):
        # ProjectActionCommandFactoryBase:45 — the colon introduces nothing.
        assert e.pi_not_active('jdoe') == 'PI jdoe is not an active user: '

    def test_manager_not_in_database_keeps_its_dangling_colon_space(self):
        # :58 — and note the PI equivalent below has NO trailing punctuation.
        assert e.manager_not_in_database('jdoe') == 'Allocation Manager jdoe is not in database: '

    def test_manager_not_active_ends_with_a_bare_space(self):
        # :60 — a space, not a colon, and different wording from the PI variant.
        assert e.manager_not_active('jdoe') == 'Allocation Manager jdoe is not active '

    def test_awarded_amount_conversion_has_two_spaces(self):
        # ProjectAllocationActionCommandsFactoryBase:66 concatenates '"' and '"  to float'.
        assert e.could_not_convert_amount('1e9x') == 'Could not convert awarded amount "1e9x"  to float'
        assert '"  to float' in e.could_not_convert_amount('x')

    def test_pi_not_in_database_has_no_trailing_punctuation(self):
        # The asymmetry with manager_not_in_database is legacy's, and is load-bearing
        # only in the sense that reproducing it keeps saved operator greps working.
        assert e.pi_not_in_database('jdoe') == 'PI jdoe is not in database'
        assert not e.pi_not_in_database('jdoe').endswith(' ')


class TestStringsMissingFromTheOldSpec:
    """§ 3.4 of XRAS_REIMPLEMENTATION.md omits or mangles these. Pinned so the
    verified table stays the one in force."""

    def test_missing_date_is_two_strings_not_one_slashed_one(self):
        assert e.missing_date('begin') == 'Missing begin date for allocation(s)'
        assert e.missing_date('end') == 'Missing end date for allocation(s)'

    def test_could_not_convert_date_exists(self):
        assert e.could_not_convert_date('begin') == 'Could not convert begin date for allocation(s)'
        assert e.could_not_convert_date('end') == 'Could not convert end date for allocation(s)'

    def test_no_fos_objects_exists(self):
        assert e.no_fos_objects() == 'No FieldOfScience (fos) objects'

    def test_no_allocation_type_for_pair_reproduces_java_tostring(self):
        assert (e.no_allocation_type_for_pair('UNIV USS', 'Small')
                == "No AllocationType for SelectionParms{panel='UNIV USS', type='Small'}")

    def test_transfer_has_three_arity_strings_not_two(self):
        assert e.transfer_one_source_only() == 'Transfer supports only one source (negative amount)'
        assert e.transfer_requires_source() == 'Transfer requires one source resource (negative amount)'
        assert (e.transfer_requires_destination()
                == 'Transfer requires at least one destination resource (positive amount)')
        assert len({e.transfer_one_source_only(),
                    e.transfer_requires_source(),
                    e.transfer_requires_destination()}) == 3


class TestTheTwoEndDateValidators:
    """Extension and Update reject the same condition with different strings, and the
    difference is how an operator tells which path rejected them."""

    def test_extension_interpolates_a_date_and_says_is_before(self):
        # ExtendProjectAllocationActionCommandsFactory:42 — UFSU0023's actual string.
        assert (e.extension_end_date_before_existing('2033-07-31')
                == 'Action end date is before existing allocation end date (2033-07-31)')

    def test_update_interpolates_a_resource_and_omits_is(self):
        # UpdateProjectAllocationActionCommandsFactory:52
        assert (e.update_end_date_before_existing('Derecho')
                == 'Action end date before existing allocation end date for Derecho')

    def test_the_two_are_not_interchangeable(self):
        assert (e.extension_end_date_before_existing('2033-07-31')
                != e.update_end_date_before_existing('2033-07-31'))
        assert ' is before ' in e.extension_end_date_before_existing('x')
        assert ' is before ' not in e.update_end_date_before_existing('x')


class TestResourceVariants:
    """Two resource-not-found strings, keyed on different inputs, both reachable in
    one action — the allocation path reports the key, the roster path the name."""

    def test_key_variant(self):
        assert e.no_resource_for_key('derecho-cpu') == 'No resource found in SAM corresponding to key derecho-cpu'

    def test_name_variant(self):
        assert e.no_resource_for_name('Derecho') == 'No resource found in SAM corresponding to name Derecho'


class TestTransferCreditFormatting:
    """Java's %f is six decimal places; Python's float repr is not."""

    def test_amounts_render_with_six_decimal_places(self):
        assert (e.transfer_credit_exceeds_debit(1000.0, 500.0)
                == 'Transfer destination credit (1000.000000) exceeds source allowed debit (500.000000)')

    def test_not_pythons_float_repr(self):
        assert '1000.0)' not in e.transfer_credit_exceeds_debit(1000.0, 500.0)


class TestActionErrorsAccumulator:
    """Mirrors legacy's LinkedHashSet: insertion-ordered AND deduplicating."""

    def test_duplicate_messages_collapse(self):
        # Three resources each missing an amount must yield ONE line, as legacy does.
        errs = ActionErrors()
        for _ in range(3):
            errs.report(e.awarded_amount_missing())
        assert list(errs) == ['Awarded amount missing']
        assert len(errs) == 1

    def test_distinct_messages_keep_insertion_order(self):
        errs = ActionErrors()
        errs.report(e.missing_title())
        errs.report(e.missing_pi_role())
        errs.report(e.no_fos_objects())
        assert list(errs) == [
            'Missing title',
            'Missing pi role',
            'No FieldOfScience (fos) objects',
        ]

    def test_a_repeat_does_not_move_a_message_to_the_end(self):
        # dict preserves the FIRST insertion position on re-assignment. An operator
        # reads the list in assembly order; a late duplicate must not reorder it.
        errs = ActionErrors()
        errs.report(e.missing_title())
        errs.report(e.missing_pi_role())
        errs.report(e.missing_title())
        assert list(errs) == ['Missing title', 'Missing pi role']

    def test_messages_differing_only_by_interpolation_are_distinct(self):
        errs = ActionErrors()
        errs.report(e.username_missing('alice'))
        errs.report(e.username_missing('bob'))
        assert len(errs) == 2

    def test_empty_is_falsey_and_full_is_truthy(self):
        errs = ActionErrors()
        assert not errs
        errs.report(e.missing_title())
        assert errs

    def test_extend_accepts_an_iterable(self):
        errs = ActionErrors()
        errs.extend([e.missing_title(), e.missing_pi_role(), e.missing_title()])
        assert list(errs) == ['Missing title', 'Missing pi role']

    def test_constructor_accepts_an_iterable_and_dedupes(self):
        errs = ActionErrors([e.missing_title(), e.missing_title()])
        assert list(errs) == ['Missing title']


class TestRaiseIfAny:
    """The throwExceptionIfErrors moment — once, between assemble and execute."""

    def test_silent_when_clean(self):
        ActionErrors().raise_if_any()  # must not raise

    def test_raises_with_every_message_in_order(self):
        errs = ActionErrors()
        errs.report(e.missing_title())
        errs.report(e.pi_not_in_database('jdoe'))
        with pytest.raises(XrasActionRejected) as exc:
            errs.raise_if_any()
        assert exc.value.messages == ['Missing title', 'PI jdoe is not in database']

    def test_str_is_newline_joined_like_ActionProcessingException(self):
        # ActionProcessingException.getMessage() is StringUtils.join(messages, "\n").
        errs = ActionErrors([e.missing_title(), e.missing_pi_role()])
        with pytest.raises(XrasActionRejected) as exc:
            errs.raise_if_any()
        assert str(exc.value) == 'Missing title\nMissing pi role'


class TestEveryBuilderIsExported:
    """The package re-exports the vocabulary; a new string added to errors.py and
    forgotten in __init__ is invisible to handlers importing from `sam.xras`."""

    def test_all_public_builders_are_reachable_from_the_package(self):
        import sam.xras as pkg

        builders = {
            name for name in dir(e)
            if not name.startswith('_') and callable(getattr(e, name))
            and getattr(e, name).__module__ == e.__name__
            and name not in {'ActionErrors', 'XrasActionRejected'}
        }
        missing = sorted(b for b in builders if not hasattr(pkg, b))
        assert missing == [], f'not exported from sam.xras: {missing}'


class TestMnemonicMessagesKeepTheLegacyPrefix:
    """The bare legacy sentences are the prefixes everything else keys on."""

    def test_no_arguments_is_the_bare_legacy_sentence(self):
        from sam.xras.errors import (
            MNEMONIC_EXTERNAL_PREFIX, MNEMONIC_INTERNAL_PREFIX,
            mnemonic_external_failed, mnemonic_internal_failed,
        )
        assert mnemonic_external_failed() == MNEMONIC_EXTERNAL_PREFIX == \
            'Could not determine Mnemonic code for external PI via institution'
        assert mnemonic_internal_failed() == MNEMONIC_INTERNAL_PREFIX == \
            'Could not determine Mnemonic code for internal PI via organization'

    def test_the_named_form_starts_with_the_sentence(self):
        from sam.xras.errors import mnemonic_external_failed, mnemonic_internal_failed
        ext = mnemonic_external_failed('kheyblom', 'UNIVERSITY OF VICTORIA', 'Victoria',
                                       [('UNIVERSITY OF MICHIGAN', 'MIC'), ('ELSEWHERE', None)])
        assert ext == ('Could not determine Mnemonic code for external PI via institution: '
                       'kheyblom\'s current institution "UNIVERSITY OF VICTORIA" (Victoria) '
                       'has no mnemonic link; also current: "UNIVERSITY OF MICHIGAN" -> MIC, '
                       '"ELSEWHERE" (no mnemonic link)')
        internal = mnemonic_internal_failed('pengz', 'CGD Admin', 'CGD')
        assert internal == ('Could not determine Mnemonic code for internal PI via '
                            'organization: pengz\'s organization "CGD Admin" (lab "CGD") '
                            'has no mnemonic link')

    def test_alternatives_are_capped_at_three(self):
        from sam.xras.errors import mnemonic_external_failed
        msg = mnemonic_external_failed('u', 'A', None, [(f'X{i}', None) for i in range(6)])
        assert msg.count('no mnemonic link') == 1 + 3

