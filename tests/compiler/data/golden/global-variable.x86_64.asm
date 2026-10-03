※ symbolic assembler dump; internal form, not a syntax

section .rodata
counter:
                             ※ align 1
    2a                       ※ data

section .text executable
main(ptr<Init>)u6:
                             ※ align 16
    0f b6 05 00 00 00 00     movzx eax, [rip + counter]   ※ fixup pcrel32 → counter
    c3                       ret
