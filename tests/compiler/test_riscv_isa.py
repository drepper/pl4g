"""The RISC-V naming convention, read.

What a program is built for on this architecture is a list and not a name, and
the architecture gives the list a spelling.  These are the rules of that
spelling, one test apiece, plus the profiles -- the published sets that exist so
that nobody has to write the list.
"""

from __future__ import annotations

import pytest

from pypl4g.target.riscv64 import isa


def test_the_base_says_how_wide_an_address_is() -> None:
    """Which of the two widths, and whether the base is the reduced one."""
    assert isa.parse("rv64i").bits == 64
    assert isa.parse("rv32i").bits == 32
    assert not isa.parse("rv64i").embedded
    assert isa.parse("rv64e").embedded


def test_the_strings_are_case_insensitive() -> None:
    """The convention says so, and a build script that shouts is still a build
    script."""
    assert isa.parse("RV64GC_Zba").render() == isa.parse("rv64gc_zba").render()


def test_g_is_the_general_purpose_set() -> None:
    """One letter for seven extensions, which is the one abbreviation the
    convention gives a letter of its own -- and it is not left beside what it
    stands for, being an abbreviation and not an extension."""
    found = isa.parse("rv64g")
    for name in ("i", "m", "a", "f", "d", "zicsr", "zifencei"):
        assert found.has(name), name
    assert not found.has("g")


def test_an_extension_brings_what_it_implies() -> None:
    """`b` is three extensions under one letter, and `d` needs `f` under it."""
    found = isa.parse("rv64ib")
    assert found.has("zba") and found.has("zbb") and found.has("zbs")
    assert isa.parse("rv64id").has("f")
    assert isa.parse("rv64i_zfa").has("f")


def test_a_version_may_be_written_after_anything() -> None:
    """Major and minor with a `p` between them, and `p0` left off."""
    found = isa.parse("rv64i2p1_m2p0_zfa1p0")
    assert found.versions["i"] == (2, 1)
    assert found.versions["m"] == (2, 0)
    assert found.versions["zfa"] == (1, 0)
    assert isa.parse("rv64i2").versions["i"] == (2, 0)


def test_a_name_ending_in_digits_is_a_name() -> None:
    """`sv39` is an extension and not `sv` at version 39.  The architecture's
    own rule is that a name never ends in a digit and `sv39` is older than the
    rule, so what settles it is which of the two readings is a name."""
    found = isa.parse("rv64gc_sv39_zic64b")
    assert found.has("sv39") and found.versions["sv39"] is None
    assert found.has("zic64b")


def test_underscores_between_single_letters_mean_nothing() -> None:
    """They are allowed for readability and say nothing."""
    assert isa.parse("rv64i_m_a_f_d").render() == isa.parse("rv64imafd").render()


def test_a_multi_letter_extension_needs_its_underscore() -> None:
    """Which is what the underscore is for: `zbazbb` would otherwise be a name
    and there would be no telling it from two written side by side."""
    with pytest.raises(isa.BadName, match="zbazbb"):
        isa.parse("rv64gc_zbazbb")


def test_an_extension_it_does_not_know_is_refused() -> None:
    """The name a program most often writes that is not an extension is a
    misspelling of one that is."""
    with pytest.raises(isa.BadName, match="zfaa"):
        isa.parse("rv64gc_zfaa")


def test_a_string_names_its_base() -> None:
    """A width on its own is not a base, and neither is a width and a `c`."""
    with pytest.raises(isa.BadName):
        isa.parse("rv64")
    with pytest.raises(isa.BadName, match="base"):
        isa.parse("rv64c")


def test_a_string_that_is_not_one_says_what_one_looks_like() -> None:
    """What a reader needs is the part they got wrong."""
    with pytest.raises(isa.BadName, match="rv32"):
        isa.parse("x86-64-v2")
    with pytest.raises(isa.BadName, match="twice"):
        isa.parse("rv64i_m_m")


def test_the_profiles_are_the_published_sets() -> None:
    """A profile is a list somebody published under one name, and the short
    name is the user-mode one, which is what a program is built for."""
    for name in ("rva23", "rva23u64", "rva23s64"):
        found = isa.parse(name)
        assert found.bits == 64
        # Among much else: the two the code generator asks about.
        assert found.floats, name
        assert found.has("zfa"), name
        assert found.has("v") and found.has("b"), name
    assert isa.parse("rva23").render() == isa.parse("rva23u64").render()
    # The supervisor profile is the user one and the privileged half beside it.
    assert isa.parse("rva23s64").has("sv39")
    assert not isa.parse("rva23u64").has("sv39")


def test_a_profile_it_does_not_know_says_which_it_knows() -> None:
    """The shape is right and the name is not one, which is a different thing
    from a string that is not a string."""
    with pytest.raises(isa.BadName, match="rva23u64"):
        isa.parse("rva99u64")


def test_the_default_is_the_newest_application_profile() -> None:
    """Which is what settles that floating point is there: the architecture's
    Linux ABI has required it from the beginning, and a default of the bare base
    would have had the compiler refuse it."""
    assert isa.DEFAULT == "rva23"
    assert isa.parse(isa.DEFAULT).floats


def test_the_canonical_form_is_the_architecture_s_order() -> None:
    """A set is a set however it was spelled, and this is how it is written
    back: the single letters in the architecture's own order, then the rest."""
    assert isa.parse("rv64i_zba_c_m").render() == "rv64imc_zba"
    assert isa.parse("rv64idfam").render() == "rv64imafd_zicsr"
