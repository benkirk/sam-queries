SHELL       := /bin/bash
.SHELLFLAGS := -e -o pipefail -c

USAGE_DIR   := /glade/u/hdig/project_user_usage
RUN         := ./run_ncar_accounting.sh

STAMPS := .quasar.stamp .glade.stamp

.PHONY: all clean
all: $(STAMPS)

# ACCT_RULE(stamp-key, glob-key, --resource value)
define ACCT_RULE
LATEST_$(1) := $$(shell ls -t $$(USAGE_DIR)/acct.$(2).????-??-?? 2>/dev/null | head -1)

ifneq ($$(LATEST_$(1)),)
.$(1).stamp: $$(LATEST_$(1))
	$$(RUN) --resource $(3) --user-usage "$$<"
	date > $$@
else
.$(1).stamp:
	@echo "No acct.$(2).* found in $$(USAGE_DIR); skipping."
endif
endef

$(eval $(call ACCT_RULE,quasar,quasar,Quasar))
$(eval $(call ACCT_RULE,glade,glade,Campaign_Store))

clean:
	rm -f $(STAMPS)

distclean:
	git clean -xdf . --exclude .env
