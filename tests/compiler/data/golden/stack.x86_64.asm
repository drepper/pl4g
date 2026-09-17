※ symbolic assembler dump; internal form, not a syntax

section .data writable
__pl4g_stack_state:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ※ data

section .rodata
.Lstack.message:
    70 6c 34 67 3a 20 74 68 65 20 73 74 61 63 6b 20 72 61 6e 20 6f 75 74 0a ※ data

section .text executable
main()u6:
                             ※ align 16
    31 c0                    xor eax, eax
    c3                       ret
    cc cc cc cc cc cc cc cc cc cc cc cc cc ※ align 16
__pl4g_stack_fault:
    48 8b 56 10              mov rdx, [rsi + 16]
    48 8d 0d 00 00 00 00     lea rcx, [rip + __pl4g_stack_state]   ※ fixup pcrel32 → __pl4g_stack_state
    4c 8b 19                 mov r11, [rcx]
    4c 39 da                 cmp rdx, r11
    0f 82 00 00 00 00        jb .L__pl4g_stack_fault.not.the.guard.1   ※ fixup pcrel32 → .L__pl4g_stack_fault.not.the.guard.1
    4c 8b 59 08              mov r11, [rcx + 8]
    4c 39 da                 cmp rdx, r11
    0f 83 00 00 00 00        jae .L__pl4g_stack_fault.not.the.guard.1   ※ fixup pcrel32 → .L__pl4g_stack_fault.not.the.guard.1
    48 c7 c7 02 00 00 00     mov rdi, 2
    48 8d 35 00 00 00 00     lea rsi, [rip + .Lstack.message]   ※ fixup pcrel32 → .Lstack.message
    48 c7 c2 18 00 00 00     mov rdx, 24
    b8 01 00 00 00           mov eax, 1
    0f 05                    syscall
    48 c7 c7 43 00 00 00     mov rdi, 67
    b8 e7 00 00 00           mov eax, 231
    0f 05                    syscall
    0f 0b                    ud2
.L__pl4g_stack_fault.not.the.guard.1:
    48 89 ce                 mov rsi, rcx
    48 c7 c7 30 00 00 00     mov rdi, 48
    48 01 fe                 add rsi, rdi
    48 c7 c7 0b 00 00 00     mov rdi, 11
    48 c7 c2 00 00 00 00     mov rdx, 0
    49 c7 c2 08 00 00 00     mov r10, 8
    b8 0d 00 00 00           mov eax, 13
    0f 05                    syscall
    c3                       ret
    cc cc cc cc cc cc        ※ align 16
__pl4g_stack_return:
    b8 0f 00 00 00           mov eax, 15
    0f 05                    syscall
    0f 0b                    ud2
    cc cc cc cc cc cc cc     ※ align 16
_start:
    31 ed                    xor ebp, ebp
    48 89 e3                 mov rbx, rsp
    49 c7 c6 00 40 31 00     mov r14, 3227648
    4c 29 f3                 sub rbx, r14
    49 c7 c6 ff ff 1f 00     mov r14, 2097151
    49 f7 d6                 not r14
    4c 21 f3                 and rbx, r14
    48 89 df                 mov rdi, rbx
    48 c7 c6 00 40 11 00     mov rsi, 1130496
    48 c7 c2 03 00 00 00     mov rdx, 3
    49 c7 c2 22 40 00 00     mov r10, 16418
    49 c7 c0 00 00 00 00     mov r8, 0
    49 f7 d0                 not r8
    49 c7 c1 00 00 00 00     mov r9, 0
    b8 09 00 00 00           mov eax, 9
    0f 05                    syscall
    48 89 c3                 mov rbx, rax
    49 c7 c6 00 10 00 00     mov r14, 4096
    49 f7 d6                 not r14
    4c 39 f3                 cmp rbx, r14
    0f 87 00 00 00 00        ja .L_start.stack.as.it.was.2   ※ fixup pcrel32 → .L_start.stack.as.it.was.2
    48 89 df                 mov rdi, rbx
    48 c7 c6 00 00 01 00     mov rsi, 65536
    48 c7 c2 00 00 00 00     mov rdx, 0
    b8 0a 00 00 00           mov eax, 10
    0f 05                    syscall
    49 c7 c6 00 00 00 00     mov r14, 0
    4c 39 f0                 cmp rax, r14
    0f 84 00 00 00 00        je .L_start.stack.guarded.3   ※ fixup pcrel32 → .L_start.stack.guarded.3
    48 89 df                 mov rdi, rbx
    48 c7 c6 00 40 11 00     mov rsi, 1130496
    b8 0b 00 00 00           mov eax, 11
    0f 05                    syscall
    e9 00 00 00 00           jmp .L_start.stack.as.it.was.2   ※ fixup pcrel32 → .L_start.stack.as.it.was.2
.L_start.stack.guarded.3:
    4c 8d 35 00 00 00 00     lea r14, [rip + __pl4g_stack_state]   ※ fixup pcrel32 → __pl4g_stack_state
    49 89 1e                 mov [r14], rbx
    49 c7 c7 00 00 01 00     mov r15, 65536
    49 01 df                 add r15, rbx
    4d 89 7e 08              mov [r14 + 8], r15
    48 c7 c3 00 00 10 00     mov rbx, 1048576
    49 01 df                 add r15, rbx
    4d 89 7e 50              mov [r14 + 80], r15
    48 c7 c3 00 00 00 00     mov rbx, 0
    49 89 5e 58              mov [r14 + 88], rbx
    48 c7 c3 00 40 00 00     mov rbx, 16384
    49 89 5e 60              mov [r14 + 96], rbx
    4c 89 f7                 mov rdi, r14
    48 c7 c6 50 00 00 00     mov rsi, 80
    48 01 f7                 add rdi, rsi
    48 c7 c6 00 00 00 00     mov rsi, 0
    b8 83 00 00 00           mov eax, 131
    0f 05                    syscall
    48 8d 1d 00 00 00 00     lea rbx, [rip + __pl4g_stack_fault]   ※ fixup pcrel32 → __pl4g_stack_fault
    49 89 5e 10              mov [r14 + 16], rbx
    48 c7 c3 04 00 00 0c     mov rbx, 201326596
    49 89 5e 18              mov [r14 + 24], rbx
    48 8d 1d 00 00 00 00     lea rbx, [rip + __pl4g_stack_return]   ※ fixup pcrel32 → __pl4g_stack_return
    49 89 5e 20              mov [r14 + 32], rbx
    4c 89 f6                 mov rsi, r14
    48 c7 c7 10 00 00 00     mov rdi, 16
    48 01 fe                 add rsi, rdi
    48 c7 c7 0b 00 00 00     mov rdi, 11
    48 c7 c2 00 00 00 00     mov rdx, 0
    49 c7 c2 08 00 00 00     mov r10, 8
    b8 0d 00 00 00           mov eax, 13
    0f 05                    syscall
    4c 89 fc                 mov rsp, r15
.L_start.stack.as.it.was.2:
    e8 00 00 00 00           call main()u6   ※ fixup pcrel32 → main()u6
    89 c7                    mov edi, eax
    b8 e7 00 00 00           mov eax, 231
    0f 05                    syscall
    0f 0b                    ud2
