※ symbolic assembler dump; internal form, not a syntax

section .rodata
counter:
                             ※ align 1
    2a                       ※ data

section .text executable
main(ptr<Init>)u6:
                             ※ align 16
    00 00 00 90              adrp x0, counter   ※ fixup aarch64_adr_page21 → counter
    00 00 00 91              add.lo12 x0, x0, counter   ※ fixup aarch64_add_lo12 → counter
    00 00 40 39              ldrb w0, x0, 0
    c0 03 5f d6              ret
