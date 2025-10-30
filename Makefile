clean:
	git clean -nxdf --exclude ".env"

clobber:
	$(MAKE) clean

%.py : %.ipynb Makefile
	jupyter nbconvert --clear-output $<
	jupyter nbconvert --to=python $< >/dev/null
	chmod +x $@
	git add $@ $<
