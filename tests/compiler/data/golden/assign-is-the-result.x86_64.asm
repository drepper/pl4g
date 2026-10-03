※ symbolic assembler dump; internal form, not a syntax

section .data writable
counter:
                             ※ align 1
    01                       ※ data
    cc cc cc cc cc cc cc     ※ align 8

section .text executable
main(ptr<Init>)u6:
                             ※ align 16
    c6 05 00 00 00 00 2a     mov [rip + counter], 42   ※ fixup pcrel32 → counter
    0f b6 05 00 00 00 00     movzx eax, [rip + counter]   ※ fixup pcrel32 → counter
    c3                       ret
