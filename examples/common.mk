# Shared rules for the examples.
#
# An example's Makefile sets EXAMPLE to the base name of its source file and
# includes this file.  Everything else follows from that.
#
# The list of architectures is not written down anywhere here: it is what the
# compiler reports, so an example builds for every target that exists at the
# time it is built and needs no editing when a target is added.

TOP     := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))/..
PYPL4G  ?= $(TOP)/bin/pypl4g
OPTLEVEL ?= 1
BUILDDIR ?= build

# The triples the compiler can generate for, one per target.
TRIPLES := $(shell $(PYPL4G) --print-targets)

# The architecture is the first component of a triple.
arch = $(firstword $(subst -, ,$(1)))

SOURCE   := $(EXAMPLE).pl4g
BINARIES := $(foreach t,$(TRIPLES),$(BUILDDIR)/$(t)/$(EXAMPLE))

# Running a binary for the host architecture needs nothing; running one for
# another architecture goes through the emulator for that architecture.
HOSTARCH := $(shell uname -m)
runner = $(if $(filter $(HOSTARCH),$(call arch,$(1))),,qemu-$(call arch,$(1)))

.PHONY: all run clean list
.DEFAULT_GOAL := all

all: $(BINARIES)

# One rule builds the example for every target: the triple is the directory the
# binary is asked for in.
$(BUILDDIR)/%/$(EXAMPLE): $(SOURCE)
	@mkdir -p $(@D)
	$(PYPL4G) --target=$* -O$(OPTLEVEL) -o $@ $<

# Build and run, reporting the status each binary exits with.  A target whose
# emulator is not installed is skipped rather than failing the run.
run: all
	@$(foreach t,$(TRIPLES), \
	  run=$(call runner,$(t)); \
	  if [ -z "$$run" ] || command -v "$$run" >/dev/null 2>&1; then \
	    $$run $(BUILDDIR)/$(t)/$(EXAMPLE); \
	    printf '%-24s exit %d\n' '$(t)' "$$?"; \
	  else \
	    printf '%-24s skipped, %s is not installed\n' '$(t)' "$$run"; \
	  fi; )

# What would be built, without building it.
list:
	@printf '%s\n' $(TRIPLES)

clean:
	rm -rf $(BUILDDIR)
