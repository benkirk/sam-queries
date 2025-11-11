SHELL := /bin/bash

config_env := module load conda >/dev/null 2>&1 || true


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

%: %.yml
	[ -d $@ ] && mv $@ $@.old && rm -rf $@.old &
	$(MAKE) solve-$*
	$(config_env) && conda env create --file $< --prefix $@
	$(config_env) && conda activate ./$@ && conda-tree deptree --small 2>/dev/null || /bin/true
	$(config_env) && conda activate ./$@ && pipdeptree --all 2>/dev/null || /bin/true
	$(config_env) && conda activate ./$@ && conda list

solve-%: %.yml
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

# this rule invokes emacs on each source file to remove trailing whitespace.
trim-whitepace:
	for file in $$(git ls-files); do \
          echo $$file ; \
          echo emacs -batch $$file --eval '(delete-trailing-whitespace)' -f save-buffer 2>/dev/null ; \
        done
