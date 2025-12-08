.ONESHELL:
SHELL := /bin/bash

CONDA_ROOT := $(shell conda info --base)

# common way to inialize enviromnent across various types of systems
config_env := module load conda >/dev/null 2>&1 || true && . $(CONDA_ROOT)/etc/profile.d/conda.sh

clean:
	rm *~

clobber:
	git clean -xdf --exclude ".env" --exclude "conda-env/"

distclean:
	$(MAKE) clobber
	rm -rf conda-env/


%.py : %.ipynb Makefile
	jupyter nbconvert --clear-output $<
	jupyter nbconvert --to=python $< >/dev/null
	chmod +x $@
	git add $@ $<

%: %.yaml pyproject.toml
	[ -d $@ ] && mv $@ $@.old && rm -rf $@.old &
	$(config_env) &&\
	conda env create --file $< --prefix $@ &&\
	conda activate ./$@ &&\
	pip install -e ".[test]" &&\
	pipdeptree --all 2>/dev/null || true

solve-%: %.yaml
	$(config_env) && conda env create --file $< --prefix $@ --dry-run

fixperms:
	for file in .env; do \
	  setfacl --remove-all $${file} ; \
	  for group in csgteam csg hdt nusd hsg; do \
	    setfacl -m g:$${group}:r $${file} ; \
	  done ;\
	  for user in bdobbins; do \
	    getent passwd $${user} 2>&1 >/dev/null || continue ; \
	    setfacl -m u:$${user}:r $${file} ; \
	  done ;\
	  getfacl $${file} ;\
	done
check:
	$(config_env) && source etc/config_env.sh && python3 tests/tools/orm_inventory.py
	$(config_env) && source etc/config_env.sh && python3 -m pytest -v
