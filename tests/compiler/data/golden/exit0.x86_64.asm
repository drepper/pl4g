※ symbolic assembler dump; internal form, not a syntax

section .text executable
main(ptr<Init>)u6:
                             ※ align 16
    48 89 c1                 mov rcx, rax
    31 c0                    xor eax, eax
    c3                       ret
