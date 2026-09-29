※ symbolic assembler dump; internal form, not a syntax

section .data writable
counter:
                             ※ align 1
    01                       ※ data

section .text executable
main()u6:
                             ※ align 16
    c6 05 00 00 00 00 07     mov [rip + counter], 7   ※ fixup pcrel32 → counter
    0f b6 05 00 00 00 00     movzx eax, [rip + counter]   ※ fixup pcrel32 → counter
    c3                       ret
.Lpl4g.frames.end:
    cc                       ※ align 16
_start:
    31 ed                    xor ebp, ebp
    e8 00 00 00 00           call main()u6   ※ fixup pcrel32 → main()u6
    89 c7                    mov edi, eax
    b8 e7 00 00 00           mov eax, 231
    0f 05                    syscall
    0f 0b                    ud2
