※ symbolic assembler dump; internal form, not a syntax

section .text executable
main(ptr<Init>)u6:
                             ※ align 16
    b8 2a 00 00 00           mov eax, 42
    c3                       ret
