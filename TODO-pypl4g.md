To Do List for the pypl4g compiler
==================================

[x] By default, all functions and variables are not visible to the outside, including when used as a module.  The `@[export]` attribute
    can be attached to a function or variable.  This also determines ELF symbol visibility.

[x] implement the `@[ignore(NUMBER)]` attribute which is defined as current `@[expect(NUMBER)]` is implemented.  The latter is similarly
    defined except that it is an error if the error/warning is not present

[x] global variables not defined `mut` are truly constants and should be defined in the `.rodata` section in the ELF file.  Done: the
    image writer groups the mapped sections by the permissions they need rather than by a writable flag, so a read-only section that
    is neither writable nor executable gets a loadable segment and a page of its own.

[x] non-`mut` local variables for which no reference is kept can be entirely dropped.  Done: a dead-code sweep removes any
    instruction nothing uses and that has no effect, which is the same question and stays right once a reference to a local can be
    kept, since a reference will be a use.  Each instruction shape says for itself whether it has an effect.

[ ] a local that was dropped should be defined as a constant expression in the debug information, so that a debugger can still
    show it.  Waits on there being any debug information at all.

[ ] functions not called, not exported, and not referenced can be dropped and should not appear in the binary.

[ ] implement module system.  A module is loaded at compile-time.  The syntax is `let modname := import("somename")` where `modname`
    is the name the module is known as in the compilation unit and `somename` is the name of the module.  There will be built-in
    modules in future, at some point.  For now modules are PL4G source files which are loaded.  They are searched for by a path
    and depending on whether the `somename` string (implement strings) contains a `/`.
    - modules which names staring with `/` are naming absolute files and only the addressed location is searched
    - the directory of the file is searched with the file name appended (even if the name contains a `/` somewhere)
    - a path defined by build rules (for now just a command line parameter) is searched, one of the colon-separated directory
      names at a time.  Absolute path names are used as is, others are searched relative to the files source or the current
      working directory, in that order
    - if still not found, look in system directories which are used by the installation.  This does not happen if the name
      contains a slash.
    The file name has the extension `.pl4g` appended if it does not already have it.
    Once a file is located the file name is remembered.  Before any future import is about to look for a file, a check is
    performed to see whether it is already known and if yes, the loaded data is shared.
    Symbols in a module are known within the source as `modname.NAME` where `NAME` is the name of the exported object in the imported
    file and `modname` is the variable from the assignment.  When generating a symbol name for the ELF symbol name the name of the
    importing module is prepended to the name of the imported module and then the object name is appended.  A module's name can
    consist of multiple concatenated module names.  The modules name is the basename part of the file name, without the `.pl4g`
    extension.  In case of a conflict with another module with the same basename append the hash sum of the respective full path.
    In case a module is imported more than once only one instance is used and the name which is used is the shortest and in case
    of a tie in length, the one sorting first.

[ ] patching a binary while it is in use.  spec/details.md asks for hooks into the system that controls binary creation so that a
    binary can be changed while it is being used.  Linux refuses to write to a running executable's file, so this needs a concrete
    mechanism -- writing to the process's memory, a `memfd`-backed scheme, or a supervisor built into the generated runtime -- and
    none has been chosen.

[ ] report a value written to a variable at the top level that nothing reads.  The rule that catches one inside a function should
    apply, but whether anything reads a variable at the top level is a question for a pass over the whole program rather than for
    the scope that defines it.

[ ] materialize a constant wider than one instruction can carry.  It needs a sequence -- two move-wide instructions on one
    architecture, an upper-immediate load and an add on another -- and none is generated, so such a constant is reported instead.

[ ] materialize the address of a symbol on RISC-V.  The rule adopted for position-independent code is that an address is only ever
    produced by one helper emitting a program-counter-relative computation.  On x86-64 that is one instruction and on AArch64 a
    pair whose halves are independent; on RISC-V the pair is not, since the second instruction's relocation refers to the label of
    the first rather than to its own address.  The backend has neither relocation rather than half of the pair, and the two
    instructions are present only in the form taking a plain immediate.

[ ] set the RISC-V header flags.  The ELF header of a RISC-V image carries flags saying which extensions the code uses and which
    floating-point convention it follows.  Zero is correct while only the base integer set is emitted; emitting floating point
    will mean setting them, and the image writer has no field for them yet.
