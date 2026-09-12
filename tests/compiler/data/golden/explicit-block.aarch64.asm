※ symbolic assembler dump; internal form, not a syntax

section .text executable
main:
                             ※ align 16
    e0 00 80 52              movz w0, 7
    c0 03 5f d6              ret
    00 00 00 00 00 00 00 00  ※ align 16
_start:
    fd 03 1f aa              mov x29, xzr
    fe 03 1f aa              mov x30, xzr
    00 00 00 94              bl main   ※ fixup aarch64_branch26 → main
    c8 0b 80 d2              movz x8, 94
    01 00 00 d4              svc 0
    20 00 20 d4              brk 1
