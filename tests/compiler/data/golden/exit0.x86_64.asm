※ symbolic assembler dump; internal form, not a syntax

section .text executable
main:
                             ※ align 16
    31 c0                    xor eax, eax
    c3                       ret
    cc cc cc cc cc cc cc cc cc cc cc cc cc ※ align 16
_start:
    31 ed                    xor ebp, ebp
    e8 00 00 00 00           call main   ※ fixup pcrel32 → main
    89 c7                    mov edi, eax
    b8 e7 00 00 00           mov eax, 231
    0f 05                    syscall
    0f 0b                    ud2
