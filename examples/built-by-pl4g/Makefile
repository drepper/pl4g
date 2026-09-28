# An example whose build is described in the language itself.
#
# Everything the other examples say here -- which files, which options, where the
# binary goes -- `build.pl4g` says instead, and this asks the compiler to read
# it once per target.  What is left for make to do is the loop and the running.

EXAMPLE := built-by-pl4g
TOP     := ../..
PYPL4G  ?= $(TOP)/bin/pypl4g
BUILDDIR ?= build

TRIPLES := $(shell $(PYPL4G) --print-targets)
arch = $(firstword $(subst -, ,$(1)))
HOSTARCH := $(shell uname -m)
runner = $(if $(filter $(HOSTARCH),$(call arch,$(1))),,qemu-$(call arch,$(1)))

.PHONY: all run clean list
.DEFAULT_GOAL := all

# One run of the compiler per target, and no source named: what to compile is
# what the build function says.
all:
	@$(foreach t,$(TRIPLES), \
	  $(PYPL4G) -Dtarget=$(t) || exit 1; )

run: all
	@$(foreach t,$(TRIPLES), \
	  run=$(call runner,$(t)); \
	  if [ -z "$$run" ] || command -v "$$run" >/dev/null 2>&1; then \
	    $$run $(BUILDDIR)/$(t)/$(EXAMPLE); \
	    printf '%-24s exit %d\n' '$(t)' "$$?"; \
	  else \
	    printf '%-24s skipped, %s is not installed\n' '$(t)' "$$run"; \
	  fi; )

list:
	@printf '%s\n' $(TRIPLES)

clean:
	rm -rf $(BUILDDIR)
