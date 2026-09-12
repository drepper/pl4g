# Build every example, for every architecture the compiler supports.
#
# The examples are the directories that hold a Makefile of their own, so adding
# an example is adding its directory; nothing here has to be edited.

EXAMPLES := $(patsubst %/Makefile,%,$(wildcard */Makefile))

.PHONY: all run clean list $(EXAMPLES)
.DEFAULT_GOAL := all

all run clean:
	@for example in $(EXAMPLES); do \
	  printf '\n==> %s\n' "$$example"; \
	  $(MAKE) --no-print-directory -C "$$example" $@ || exit $$?; \
	done

list:
	@printf '%s\n' $(EXAMPLES)

$(EXAMPLES):
	@$(MAKE) --no-print-directory -C $@
