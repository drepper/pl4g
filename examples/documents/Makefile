# JSON documents decoded and encoded over and over, to time the json module by.
EXAMPLE := documents

include ../common.mk

# The three documents generate.py writes, four megabytes each, in build/.
.PHONY: data
data:
	python3 generate.py $(BUILDDIR) 4
