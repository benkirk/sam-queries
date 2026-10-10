#!/usr/bin/env python3
"""FairShare resource_group: the PBS fair-share tree for one machine, built from the
fstree data with PBS's own capacities. Arguments:
  rollup   none (default) | group | facility | all: roll shares up from actual
           allocations, to that tier
  equalize none | leaf | facility, comma-combinable: weigh peers equally, ignoring
           allocation size ('facility' conflicts with rollup=facility or all)
  format   json (default; lines + the capacities used) | text (the resource_group file)
Results are cached per argument set; clear_cache=1 recomputes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))  # the shared sam_api.py
from sam_api import ApiError, example, get, get_text, show

# The tree needs the hpc-scheduling-tools plugin, which not every deployment loads.
# A server without it answers 503: say so and stop, rather than fail.
try:
    derecho = get('/api/v1/fairshare/Derecho')
except ApiError as err:
    if err.code != 503:
        raise
    print('(this server has no fairshare plugin; skipping)')
    sys.exit(0)

example('Derecho, defaults: top-down shares from the allocations')
show({'machine': derecho['machine'], 'rollup': derecho['rollup'],
      'equalize': derecho['equalize'], 'capacities': derecho['capacities'],
      'lines': len(derecho['lines']), 'first_lines': derecho['lines'][:5],
      'warnings': len(derecho['warnings'])})

example('Derecho, rollup=facility&equalize=leaf: every project account weighs the same')
tree = get('/api/v1/fairshare/Derecho', rollup='facility', equalize='leaf')
show({'rollup': tree['rollup'], 'equalize': tree['equalize'],
      'first_lines': tree['lines'][:5], 'warnings': tree['warnings'][:3]})

example('Casper, equalize=facility: facilities split evenly, as the resource_group file')
text = get_text('/api/v1/fairshare/Casper', equalize='facility', format='text')
print('\n'.join(text.splitlines()[:10]))

example('Casper, rollup=group: shares rolled up to the project-group tier')
tree = get('/api/v1/fairshare/Casper', rollup='group')
show({'rollup': tree['rollup'], 'lines': len(tree['lines']), 'first_lines': tree['lines'][:5]})

example('A conflicting combination is a 400 that says why')
try:
    get('/api/v1/fairshare/Derecho', rollup='facility', equalize='facility')
    sys.exit('expected a 400')
except ApiError as err:
    assert err.code == 400, err
    print(err.body)
