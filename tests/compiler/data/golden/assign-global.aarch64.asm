※ symbolic assembler dump; internal form, not a syntax

section .data writable
counter:
                             ※ align 1
    01                       ※ data
    00 00 00 00 00 00 00     ※ align 8

section .text executable
main(ptr<Init>)u6:
                             ※ align 16
    e0 00 80 52              movz w0, 7
    01 00 00 90              adrp x1, counter   ※ fixup aarch64_adr_page21 → counter
    21 00 00 91              add.lo12 x1, x1, counter   ※ fixup aarch64_add_lo12 → counter
    20 00 00 39              strb w0, x1, 0
    00 00 00 90              adrp x0, counter   ※ fixup aarch64_adr_page21 → counter
    00 00 00 91              add.lo12 x0, x0, counter   ※ fixup aarch64_add_lo12 → counter
    00 00 40 39              ldrb w0, x0, 0
    c0 03 5f d6              ret
