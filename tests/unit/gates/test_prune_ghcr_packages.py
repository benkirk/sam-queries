"""The GHCR prune plan never deletes a manifest a kept tag still references."""
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[3] / '.github' / 'scripts' / 'prune_ghcr_packages.py'
_spec = importlib.util.spec_from_file_location('prune_ghcr_packages', _PATH)
prune = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prune)


def version(vid, digest, *tags):
    return {'id': vid, 'name': digest, 'metadata': {'container': {'tags': list(tags)}}}


def multiarch(vid, digest, *tags):
    """An index plus its two platform manifests and two attestations, all untagged but the index."""
    kids = [version(vid * 10 + i, f'{digest}-child{i}') for i in range(4)]
    return [version(vid, digest, *tags)] + kids, {digest: {k['name'] for k in kids}}


def build(*images):
    versions, children = [], {}
    for vs, ch in images:
        versions += vs
        children.update(ch)
    return versions, lambda d: children.get(d, set())


def deleted_ids(doomed):
    return sorted(v['id'] for v, _ in doomed)


def test_children_of_a_kept_tag_survive():
    versions, children_of = build(multiarch(1, 'sha256:new', 'sha-new', 'staging'))
    assert prune.plan(versions, 10, children_of) == []


def test_an_old_sha_goes_with_its_children():
    versions, children_of = build(multiarch(1, 'sha256:new', 'sha-new'),
                                  multiarch(2, 'sha256:old', 'sha-old'))
    assert deleted_ids(prune.plan(versions, 1, children_of)) == [2, 20, 21, 22, 23]


def test_an_orphan_untagged_version_is_deleted():
    versions, children_of = build(multiarch(1, 'sha256:new', 'main'))
    versions.append(version(99, 'sha256:orphan'))
    assert deleted_ids(prune.plan(versions, 10, children_of)) == [99]


def test_protected_and_unknown_tags_are_kept_whatever_their_age():
    versions, children_of = build(multiarch(1, 'sha256:a', 'sha-a'),
                                  multiarch(2, 'sha256:b', 'main'),
                                  multiarch(3, 'sha256:c', 'ux-branch'))
    assert prune.plan(versions, 1, children_of) == []


def test_an_unreadable_kept_manifest_keeps_every_untagged_version():
    versions, _ = build(multiarch(1, 'sha256:new', 'sha-new'),
                        multiarch(2, 'sha256:old', 'sha-old'))
    versions.append(version(99, 'sha256:orphan'))

    def unreadable(digest):
        raise OSError('registry unreachable')

    # The old tagged index still goes; nothing untagged is touched.
    assert deleted_ids(prune.plan(versions, 1, unreadable)) == [2]


@pytest.mark.parametrize('keep', [0, 2])
def test_keep_counts_only_sha_tags(keep):
    versions, children_of = build(multiarch(1, 'sha256:m', 'main'),
                                  multiarch(2, 'sha256:x', 'sha-x'),
                                  multiarch(3, 'sha256:y', 'sha-y'))
    doomed = {v['id'] for v, _ in prune.plan(versions, keep, children_of)}
    assert (2 in doomed, 3 in doomed) == ((True, True) if keep == 0 else (False, False))
