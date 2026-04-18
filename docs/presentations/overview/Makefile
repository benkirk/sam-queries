.PHONY: pptx html pdf clean

QFILE := overview.qmd
OUT   := overview

pptx:
	quarto render $(QFILE) --to pptx -o $(OUT).pptx

html:
	quarto render $(QFILE) --to revealjs -o $(OUT).html

pdf:
	quarto render $(QFILE) --to beamer -o $(OUT).pdf

clean:
	rm -f $(OUT).pptx $(OUT).html $(OUT).pdf
	rm -rf $(OUT)_files
