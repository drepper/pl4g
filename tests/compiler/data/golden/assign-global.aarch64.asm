※ symbolic assembler dump; internal form, not a syntax

section .data writable
counter:
                             ※ align 1
    01                       ※ data

section .text executable
main()u8:
                             ※ align 16
    ea 00 80 52              movz w10, 7
    09 00 00 90              adrp x9, counter   ※ fixup aarch64_adr_page21 → counter
    29 01 00 91              add.lo12 x9, x9, counter   ※ fixup aarch64_add_lo12 → counter
    2a 01 00 39              strb w10, x9, 0
    00 00 00 90              adrp x0, counter   ※ fixup aarch64_adr_page21 → counter
    00 00 00 91              add.lo12 x0, x0, counter   ※ fixup aarch64_add_lo12 → counter
    00 00 40 39              ldrb w0, x0, 0
    c0 03 5f d6              ret
_start:
                             ※ align 16
    fd 03 1f aa              mov x29, xzr
    fe 03 1f aa              mov x30, xzr
    00 00 00 94              bl main()u8   ※ fixup aarch64_branch26 → main()u8
    c8 0b 80 d2              movz x8, 94
    01 00 00 d4              svc 0
    20 00 20 d4              brk 1
