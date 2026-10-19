※ symbolic assembler dump; internal form, not a syntax

section .data writable
__pl4g_init:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ※ data
__pl4g_stack_state:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 04 00 00 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 40 00 00 00 00 00 00 ※ data

section .rodata
.Lstack.message:
    70 6c 34 67 3a 20 74 68 65 20 73 74 61 63 6b 20 72 61 6e 20 6f 75 74 0a ※ data

section .text executable
main(ptr<Init>)u6:
                             ※ align 16
    00 00 80 52              movz w0, 0
    c0 03 5f d6              ret
.Lpl4g.frames.end:
    00 00 00 00 00 00 00 00  ※ align 16
__pl4g_stack_fault:
    29 08 40 f9              ldr x9, x1, 16
    0a 00 00 90              adrp x10, __pl4g_stack_state   ※ fixup aarch64_adr_page21 → __pl4g_stack_state
    4a 01 00 91              add.lo12 x10, x10, __pl4g_stack_state   ※ fixup aarch64_add_lo12 → __pl4g_stack_state
    4b 01 40 f9              ldr x11, x10, 0
    3f 01 0b eb              cmp x9, x11
    03 00 00 54              b.lo .L__pl4g_stack_fault.not.the.guard.1   ※ fixup aarch64_branch19 → .L__pl4g_stack_fault.not.the.guard.1
    4b 05 40 f9              ldr x11, x10, 8
    3f 01 0b eb              cmp x9, x11
    02 00 00 54              b.hs .L__pl4g_stack_fault.not.the.guard.1   ※ fixup aarch64_branch19 → .L__pl4g_stack_fault.not.the.guard.1
    40 00 80 d2              movz x0, 2
    01 00 00 90              adrp x1, .Lstack.message   ※ fixup aarch64_adr_page21 → .Lstack.message
    21 00 00 91              add.lo12 x1, x1, .Lstack.message   ※ fixup aarch64_add_lo12 → .Lstack.message
    02 03 80 d2              movz x2, 24
    08 08 80 d2              movz x8, 64
    01 00 00 d4              svc 0
    c0 0a 80 d2              movz x0, 86
    c8 0b 80 d2              movz x8, 94
    01 00 00 d4              svc 0
    01 00 00 00              udf 1
.L__pl4g_stack_fault.not.the.guard.1:
    41 c1 00 91              add x1, x10, 48
    60 01 80 d2              movz x0, 11
    02 00 80 d2              movz x2, 0
    03 01 80 d2              movz x3, 8
    c8 10 80 d2              movz x8, 134
    01 00 00 d4              svc 0
    c0 03 5f d6              ret
    00 00 00 00 00 00 00 00  ※ align 16
_start:
    f5 03 00 91              add x21, sp, 0
    fd 03 1f aa              mov x29, xzr
    fe 03 1f aa              mov x30, xzr
    f6 03 00 91              add x22, sp, 0
    17 00 88 d2              movz x23, 16384, 0
    37 06 a0 f2              movk x23, 49, 16
    d6 02 17 cb              sub x22, x22, x23
    f7 ff 9f d2              movz x23, 65535, 0
    f7 03 a0 f2              movk x23, 31, 16
    f7 03 37 aa              mvn x23, x23
    d6 02 17 8a              and x22, x22, x23
    e0 03 16 aa              mov x0, x22
    01 00 88 d2              movz x1, 16384, 0
    21 02 a0 f2              movk x1, 17, 16
    62 00 80 d2              movz x2, 3
    43 04 88 d2              movz x3, 16418
    04 00 80 d2              movz x4, 0
    e4 03 24 aa              mvn x4, x4
    05 00 80 d2              movz x5, 0
    c8 1b 80 d2              movz x8, 222
    01 00 00 d4              svc 0
    f6 03 00 aa              mov x22, x0
    17 00 82 d2              movz x23, 4096
    f7 03 37 aa              mvn x23, x23
    df 02 17 eb              cmp x22, x23
    08 00 00 54              b.hi .L_start.stack.as.it.was.2   ※ fixup aarch64_branch19 → .L_start.stack.as.it.was.2
    e0 03 16 aa              mov x0, x22
    21 00 a0 d2              movz x1, 1, 16
    02 00 80 d2              movz x2, 0
    48 1c 80 d2              movz x8, 226
    01 00 00 d4              svc 0
    00 00 00 b4              cbz x0, .L_start.stack.guarded.3   ※ fixup aarch64_branch19 → .L_start.stack.guarded.3
    e0 03 16 aa              mov x0, x22
    01 00 88 d2              movz x1, 16384, 0
    21 02 a0 f2              movk x1, 17, 16
    e8 1a 80 d2              movz x8, 215
    01 00 00 d4              svc 0
    00 00 00 14              b .L_start.stack.as.it.was.2   ※ fixup aarch64_branch26 → .L_start.stack.as.it.was.2
.L_start.stack.guarded.3:
    17 00 00 90              adrp x23, __pl4g_stack_state   ※ fixup aarch64_adr_page21 → __pl4g_stack_state
    f7 02 00 91              add.lo12 x23, x23, __pl4g_stack_state   ※ fixup aarch64_add_lo12 → __pl4g_stack_state
    f6 02 00 f9              str x22, x23, 0
    38 00 a0 d2              movz x24, 1, 16
    18 03 16 8b              add x24, x24, x22
    f8 06 00 f9              str x24, x23, 8
    16 02 a0 d2              movz x22, 16, 16
    18 03 16 8b              add x24, x24, x22
    f8 2a 00 f9              str x24, x23, 80
    e0 42 01 91              add x0, x23, 80
    01 00 80 d2              movz x1, 0
    88 10 80 d2              movz x8, 132
    01 00 00 d4              svc 0
    e1 42 00 91              add x1, x23, 16
    60 01 80 d2              movz x0, 11
    02 00 80 d2              movz x2, 0
    03 01 80 d2              movz x3, 8
    c8 10 80 d2              movz x8, 134
    01 00 00 d4              svc 0
    1f 03 00 91              add sp, x24, 0
.L_start.stack.as.it.was.2:
    e0 03 15 aa              mov x0, x21
    01 00 00 90              adrp x1, __pl4g_init   ※ fixup aarch64_adr_page21 → __pl4g_init
    21 00 00 91              add.lo12 x1, x1, __pl4g_init   ※ fixup aarch64_add_lo12 → __pl4g_init
    21 80 07 91              add x1, x1, 480
    00 00 00 94              bl pl4g_args   ※ fixup aarch64_branch26 → pl4g_args
    00 00 00 90              adrp x0, __pl4g_init   ※ fixup aarch64_adr_page21 → __pl4g_init
    00 00 00 91              add.lo12 x0, x0, __pl4g_init   ※ fixup aarch64_add_lo12 → __pl4g_init
    00 00 00 94              bl main(ptr<Init>)u6   ※ fixup aarch64_branch26 → main(ptr<Init>)u6
    f3 03 00 aa              mov x19, x0
    00 00 00 90              adrp x0, __pl4g_init   ※ fixup aarch64_adr_page21 → __pl4g_init
    00 00 00 91              add.lo12 x0, x0, __pl4g_init   ※ fixup aarch64_add_lo12 → __pl4g_init
    00 c0 00 91              add x0, x0, 48
    00 00 00 94              bl pl4g_io_drain   ※ fixup aarch64_branch26 → pl4g_io_drain
    e0 03 13 aa              mov x0, x19
    c8 0b 80 d2              movz x8, 94
    01 00 00 d4              svc 0
    01 00 00 00              udf 1

section .pl4grt.text executable
pl4g_args:
.Lpl4grt.0:
                             ※ align 4
    e9 03 01 aa 60 0a 00 b4 0b 00 40 f9 ea 03 00 aa 0c 0c 0b 8b 88 09 40 f9 68 0a 00 b4 ed 03 1f aa 8c 61 00 91 05 00 00 14 88 79 6d f8 ae 05 00 91 ed 03 0e aa a8 09 00 b4 0e 01 40 39 df 41 01 71 41 ff ff 54 0e 05 40 39 df 31 01 71 e1 fe ff 54 0e 09 40 39 df d1 00 71 81 fe ff 54 0e 0d 40 39 df 1d 01 71 21 fe ff 54 0e 11 40 39 df 7d 01 71 c1 fd ff 54 0e 15 40 39 df 21 01 71 61 fd ff 54 0e 19 40 39 df 05 01 71 01 fd ff 54 0e 1d 40 39 df 4d 01 71 a1 fc ff 54 0e 21 40 39 df 21 01 71 41 fc ff 54 0e 25 40 39 df 7d 01 71 e1 fb ff 54 0e 29 40 39 df 4d 01 71 81 fb ff 54 0e 2d 40 39 df 15 01 71 21 fb ff 54 0e 31 40 39 df 15 01 71 c1 fa ff 54 0e 35 40 39 df 11 01 71 61 fa ff 54 0e 3d 00 b1 20 fa ff 54 0f 39 40 39 ff f5 00 71 c1 f9 ff 54 cd 01 40 39 6c 94 9a d2 6c 86 ba f2 ab c1 00 51 cc c5 d4 f2 7f 25 00 71 ab 14 8f d2 6c 66 e9 f2 8b 4d b5 f2 8b b9 d1 f2 ab a6 e5 f2 e8 05 00 54 ee 03 1f aa 08 41 00 91 4f 01 80 52 b0 0d 00 12 ce 41 0f 9b 0d 15 40 38 b0 c1 00 51 1f 2a 00 71 63 ff ff 54 cc 01 0c ca 24 00 00 14 08 00 00 90 3f 01 00 f9 00 01 c0 3d 20 81 80 3c 45 00 00 14 ee 03 1f aa c8 f1 7d d3 08 0d 0b 8b 0b 01 0a 8b a8 14 8f d2 88 4d b5 f2 6b 81 00 91 88 b9 d1 f2 a8 a6 e5 f2 6c 81 5f f8 cc 00 00 b4 9f 65 00 f1 40 01 00 54 6b 41 00 91 6c 81 5f f8 8c ff ff b5 2b 99 95 d2 4c 01 08 ca cb c5 b2 f2 6b 72 c9 f2 0b 77 f1 f2 09 00 00 14 6b 01 40 f9 2e 99 95 d2 ce c5 b2 f2 6c 35 40 a9 6e 72 c9 f2 0e 77 f1 f2 8b 01 08 ca ac 01 0e ca 68 7d 0c 9b 0d 00 00 90 3f 01 00 f9 6b 7d cc 9b 0c 00 00 90 80 01 c0 3d 20 81 80 3c 68 01 08 ca a8 01 00 f9 4b 85 40 f8 8b 03 00 b4 68 05 0b 8b e0 03 1f aa 62 00 80 52 43 04 80 52 04 00 80 92 e5 03 1f aa 01 f1 7d d3 c8 1b 80 52 01 00 00 d4 40 02 f8 b7 e8 03 1f aa 0c 03 80 52 05 00 00 14 08 05 00 91 ae fd 00 a9 1f 01 0b eb 80 01 00 54 0d 01 0c 9b 4e 79 68 f8 ae 01 00 f9 2e ff ff b4 ef 03 1f aa d0 69 6f 38 ef 05 00 91 d0 ff ff 35 ee 05 00 d1 f3 ff ff 17 c0 03 5f d6 20 2d 00 a9 c0 03 5f d6 ※ data
pl4g_hash:
    a9 14 8f d2 28 99 95 d2 3f 40 00 f1 89 4d b5 f2 c8 c5 b2 f2 89 b9 d1 f2 68 72 c9 f2 a9 a6 e5 f2 08 77 f1 f2 4a 00 09 ca 4b 7d 08 9b 4a 7d c8 9b 4a 01 0b ca 2b 00 02 ca 6a 01 0a ca e8 01 00 54 3f 10 00 f1 63 0b 00 54 2b fc 43 d3 2c 04 7d 92 0e 00 40 b9 8b 25 cb 9a 0c 00 01 8b 8d cd 5f b8 8c 01 0b cb 0b 68 6b b8 8f 01 40 b9 ac 81 0e aa eb 81 0b aa 6c 00 00 14 6c 94 9a d2 3f c4 00 f1 6c 86 ba f2 cc c5 d4 f2 6c 66 e9 f2 83 0a 00 54 ed 03 0a aa eb 03 01 aa ee 03 0a aa 3f 80 01 f1 63 05 00 54 0f 40 40 a9 6b 81 01 d1 11 48 41 a9 7f 7d 01 f1 ef 01 09 ca 0e 02 0e ca d0 7d 0f 9b 4d 02 0d ca 12 08 42 a9 ce 7d cf 9b 2f 02 08 ca b1 7d 0f 9b 4a 00 0a ca 02 0c 43 a9 ad 7d cf 9b 4f 02 0c ca ce 01 10 ca 52 7d 0f 9b 6e 00 0e ca 4a 7d cf 9b 4f 00 09 ca 02 0c 44 a9 ad 01 11 ca d0 7d 0f 9b ce 7d cf 9b 4f 00 08 ca 6d 00 0d ca 02 0c 45 a9 4a 01 12 ca b1 7d 0f 9b 00 80 01 91 ad 7d cf 9b 4f 00 0c ca 6a 00 0a ca ce 01 10 ca 52 7d 0f 9b 4a 7d cf 9b ad 01 11 ca 4a 01 12 ca 28 fb ff 54 7f c1 00 f1 a3 02 00 54 11 48 41 a9 6b c1 00 d1 0f 40 40 a9 4d 02 0d ca 12 08 42 a9 ef 01 09 ca 0e 02 0e ca 00 c0 00 91 d0 7d 0f 9b 4a 00 0a ca ce 7d cf 9b 2f 02 08 ca b1 7d 0f 9b ad 7d cf 9b 4f 02 0c ca ce 01 10 ca 52 7d 0f 9b 4a 7d cf 9b ad 01 11 ca 4a 01 12 ca ad 01 0e ca 7f 45 00 f1 aa 01 0a ca c2 01 00 54 1c 00 00 14 01 05 00 b4 0c 00 40 39 2d fc 41 d3 0e 00 01 8b eb 03 1f aa 8c 1d 48 d3 0d 68 6d 38 8c 81 0d aa cd f1 5f 38 8c 01 0d aa 13 00 00 14 eb 03 01 aa 0e 34 40 a9 7f 85 00 f1 ad 01 08 ca ce 01 0c ca 4a 01 0d ca 4d 7d 0e 9b 4a 7d ce 9b 4a 01 0d ca e3 00 00 54 0d 38 41 a9 ac 01 0c ca ca 01 0a ca 4d 7d 0c 9b 4a 7d cc 9b 4a 01 0d ca 0b 00 0b 8b 6c 2d 7f a9 8c 01 08 ca 4a 01 0b ca 29 00 09 ca 4b 7d 0c 9b 4a 7d cc 9b 29 01 0b ca 48 01 08 ca 0a 7d 09 9b 08 7d c9 9b 00 01 0a ca c0 03 5f d6 ec 03 1f aa eb 03 1f aa f3 ff ff 17 ※ data
pl4g_hash_word:
    a8 14 8f d2 29 99 95 d2 2c 00 00 ca 88 4d b5 f2 c9 c5 b2 f2 88 b9 d1 f2 69 72 c9 f2 a8 a6 e5 f2 09 77 f1 f2 2a 00 08 ca 08 01 7d b2 4b 7d 09 9b 4a 7d c9 9b 4a 01 0b ca 2b 81 c0 ca 8a 01 0a ca 4a 01 7d d2 4c 7d 0b 9b 4a 7d cb 9b 88 01 08 ca 49 01 09 ca 2a 7d 08 9b 28 7d c8 9b 00 01 0a ca c0 03 5f d6 ※ data
pl4g_hash_seed:
    08 00 00 90 ab 14 8f d2 2c 99 95 d2 09 00 00 90 0a 01 40 f9 8b 4d b5 f2 cc c5 b2 f2 29 01 40 f9 8b b9 d1 f2 6c 72 c9 f2 ab a6 e5 f2 4a 05 00 91 0c 77 f1 f2 29 01 0b ca 0a 01 00 f9 4b 01 0c ca 2c 7d 0b 9b 29 7d cb 9b 29 01 0c ca 3f 05 00 f1 20 85 9f 9a c0 03 5f d6 ※ data
pl4g_env:
    20 08 00 b4 08 00 40 f9 e1 05 80 92 0a 00 80 92 08 0c 08 8b 09 41 00 91 28 00 80 92 eb 03 09 aa 6c 85 40 f8 08 09 00 91 21 c0 00 91 4a 05 00 91 8c ff ff b5 2a 06 00 b4 ec 03 08 aa e0 03 1f aa c8 1b 80 52 62 00 80 52 43 04 80 52 04 00 80 92 e5 03 1f aa 01 00 00 d4 00 05 f8 b7 e8 03 1f aa 0b 06 80 52 e1 03 0c aa 0a 00 00 14 0f 01 0b 9b ec 03 1f aa ed 39 00 a9 ad 01 0e 8b 08 05 00 91 ff 35 01 a9 5f 01 08 eb ec 7d 02 a9 e0 03 00 54 2d 79 68 f8 0e 00 80 92 30 00 80 92 ec 03 0e aa ae 01 0e 8b ef 03 10 aa d1 05 40 39 8e 05 00 91 10 06 00 91 51 ff ff 35 ae fd ff b4 f0 03 1f aa b1 69 70 38 3f f6 00 71 e0 00 00 54 ef 05 00 d1 10 06 00 91 8c 05 00 d1 ff 09 00 b1 21 ff ff 54 e3 ff ff 17 0f 01 0b 9b ed 41 00 a9 ad 01 10 8b ad 05 00 91 e2 ff ff 17 e1 03 1f aa e0 03 1f aa c0 03 5f d6 e1 03 1f aa c0 03 5f d6 ※ data
pl4g_io_submit:
    ff 43 03 d1 f4 4f 0c a9 f3 03 03 aa 3f 0c 00 71 fd 7b 08 a9 fd 03 02 91 fa 67 09 a9 f8 5f 0a a9 f6 57 0b a9 03 02 00 54 85 02 00 b4 73 02 00 b4 9f 40 40 f1 09 0d 00 54 88 fc 3f 91 e0 03 13 aa e2 03 1f aa 01 cd 74 92 e8 1a 80 52 e3 03 1f aa e4 03 1f aa e5 03 1f aa 01 00 00 d4 a9 02 80 92 74 01 00 14 08 00 40 f9 c8 00 00 b4 1f 05 00 f1 f6 07 9f 1a 66 00 00 14 a9 02 80 92 6d 01 00 14 00 e4 00 6f 48 00 80 52 ed 03 01 aa ee 03 02 aa ef 03 05 aa f0 03 04 aa f1 03 00 aa 08 00 00 f9 00 01 80 52 ff 3b 00 f9 e1 03 00 91 28 35 80 52 e0 03 00 ad e2 03 1f aa e3 03 1f aa e0 03 01 ad e4 03 1f aa e5 03 1f aa e0 03 02 ad e0 1b 80 3d 01 00 00 d4 00 09 f8 b7 e8 33 40 29 ea 43 40 b9 eb 67 40 b9 e9 03 00 aa e4 03 00 aa e0 03 1f aa e5 03 1f aa 41 09 08 8b 6a 11 0c 8b 0c e5 7a d3 c8 1b 80 52 62 00 80 52 23 00 90 52 01 00 00 d4 eb 03 00 aa c8 1b 80 52 e0 03 1f aa e1 03 0a aa 62 00 80 52 23 00 90 52 05 00 a1 52 01 00 00 d4 ea 03 00 aa e0 03 1f aa c8 1b 80 52 e1 03 0c aa 62 00 80 52 23 00 90 52 05 00 a2 52 01 00 00 d4 36 00 80 52 2b 05 f8 b7 e8 03 00 aa e4 03 10 aa e5 03 0f aa e0 03 11 aa e2 03 0e aa e1 03 0d aa ea 04 f8 b7 c8 04 f8 b7 ec 33 40 b9 ed 43 40 b9 60 0d 08 4e 42 0d 08 4e e1 17 40 fd e3 2b 40 fd 08 18 00 f9 68 01 0c 8b 6b 01 0d 8b 08 2c 02 a9 e8 5b 40 b9 eb 67 40 b9 00 10 a1 2e 41 10 a3 2e f6 03 1f 2a 48 01 08 8b 4a 01 0b 8b 08 a8 04 a9 28 00 80 52 00 04 80 3d 01 80 83 3c 08 24 00 a9 0f 00 00 14 9f 04 04 f1 82 12 00 54 88 3c 00 91 9f 00 00 f1 08 fd 44 d3 08 05 00 d1 e8 03 88 9a 9c 00 00 14 36 00 80 52 e4 03 10 aa e5 03 0f aa e0 03 11 aa e2 03 0e aa e1 03 0d aa f5 03 00 aa a8 8e 45 f8 e8 0f 00 b4 09 30 40 f9 e9 0f 00 b4 0a 34 40 f9 ea 0f 00 b4 0b 38 40 f9 0b 11 00 b4 0c 3c 40 f9 0c 11 00 b4 0d 40 40 f9 0d 12 00 b4 0e 44 40 f9 0e 12 00 b4 0f 48 40 f9 0f 12 00 b4 10 00 40 f9 1f 06 00 f1 e1 0b 00 54 1f 05 00 f1 e8 17 9f 1a 3f 05 00 f1 08 15 88 1a 5f 05 00 f1 08 15 88 1a 7f 05 00 f1 08 15 88 1a 9f 05 00 f1 08 15 88 1a bf 05 00 f1 08 15 88 1a df 05 00 f1 08 15 88 1a ff 05 00 f1 e9 17 9f 1a 08 01 09 2a a8 09 00 34 09 04 40 f9 f7 03 04 aa e4 03 1f aa f4 03 00 aa f8 03 05 aa e5 03 1f aa e0 03 09 aa 48 35 80 52 f9 03 01 aa 01 01 80 52 fa 03 02 aa 22 00 80 52 23 00 80 52 01 00 00 d4 e5 03 18 aa e4 03 17 aa 80 07 f8 b7 e0 03 14 aa 08 02 00 94 88 2e 40 f9 e0 03 14 aa 28 1d 00 b4 09 30 40 f9 e4 03 17 aa e5 03 18 aa e2 03 1a aa e1 03 19 aa 49 08 00 b4 0a 34 40 f9 4a 08 00 b4 0b 38 40 f9 6b 09 00 b4 0c 3c 40 f9 6c 09 00 b4 0d 40 40 f9 6d 0a 00 b4 0e 44 40 f9 6e 0a 00 b4 0f 48 40 f9 af 1c 00 b4 10 00 40 f9 1f 06 00 f1 41 04 00 54 1f 05 00 f1 e8 17 9f 1a 3f 05 00 f1 08 15 88 1a 5f 05 00 f1 08 15 88 1a 7f 05 00 f1 08 15 88 1a 9f 05 00 f1 08 15 88 1a bf 05 00 f1 08 15 88 1a df 05 00 f1 08 15 88 1a ff 05 00 f1 e9 17 9f 1a 08 01 09 2a 08 02 00 34 00 04 40 f9 e4 03 1f aa e5 03 1f aa 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 01 00 00 d4 e5 03 18 aa e4 03 17 aa a0 00 f8 b7 e0 03 14 aa d1 01 00 94 e5 03 18 aa e4 03 17 aa e5 13 00 b4 d3 13 00 b4 9f 40 40 f1 c9 13 00 54 88 fc 3f 91 e0 03 13 aa e2 03 1f aa 01 cd 74 92 e8 1a 80 52 e3 03 1f aa e4 03 1f aa e5 03 1f aa 01 00 00 d4 09 00 80 92 89 00 00 14 e9 03 1f aa 1f 00 00 14 29 00 80 52 1d 00 00 14 49 00 80 52 1b 00 00 14 9f 04 08 f1 83 01 00 54 09 40 80 52 08 02 80 52 29 f9 7f d3 08 05 00 91 3f 01 04 eb a3 ff ff 54 06 00 00 14 69 00 80 52 10 00 00 14 89 00 80 52 0e 00 00 14 08 02 80 52 09 00 00 90 29 01 00 91 2a 79 68 f8 6a 02 00 f9 33 79 28 f8 a9 02 80 92 6e 00 00 14 a9 00 80 52 04 00 00 14 c9 00 80 52 02 00 00 14 e9 00 80 52 9f 00 00 f1 c8 02 00 52 e8 03 88 1a a8 02 00 37 e4 06 00 b4 08 00 00 90 08 01 00 91 4a 7c 40 93 08 49 21 8b ec 03 00 aa e1 03 13 aa e0 03 0a aa eb 03 04 aa e2 03 04 aa 08 05 40 79 e3 03 1f aa e4 03 1f aa ea 03 05 aa e5 03 1f aa 01 00 00 d4 e5 03 0a aa e8 03 00 aa e0 03 0c aa 26 00 00 14 0b a8 41 a9 28 7c 7e d3 0c 00 00 90 8c 01 00 91 0d 00 00 90 88 69 68 38 a0 01 c0 3d 2d f1 7d d3 4a 01 40 b9 6b fd df 88 0c 18 40 f9 6a 01 0a 0a 6b 05 00 11 8c 19 0a 8b 9f fd 02 a9 9f 1d 00 f9 80 01 80 3d 93 7d 01 a9 88 01 00 39 08 14 40 f9 82 05 00 b9 84 19 00 b9 89 11 00 f9 0c 0c 40 f9 0a 59 2a b8 28 00 80 52 8b fd 9f 88 a8 6a 2d f8 08 00 0d 8b 1f 4d 00 f9 45 06 00 b4 08 0c 09 8b 13 6d 00 f9 04 8d 00 f9 2e 00 00 14 eb 03 04 aa e8 03 1f aa 2a f1 7d d3 0c 00 0a 8b 88 4d 00 f9 48 00 80 52 a8 6a 2a f8 c5 04 00 b4 b3 04 00 b4 e8 03 0b aa 7f 41 40 f1 69 01 00 54 08 fd 3f 91 e0 03 13 aa e2 03 1f aa 01 cd 74 92 e8 1a 80 52 e3 03 1f aa e4 03 1f aa e5 03 1f aa 01 00 00 d4 18 00 00 14 1f 05 04 f1 e2 00 00 54 0a 3d 00 91 1f 01 00 f1 4a fd 44 d3 4a 05 00 d1 ea 03 8a 9a 0b 00 00 14 1f 05 08 f1 03 01 00 54 0b 40 80 52 0a 02 80 52 6b f9 7f d3 4a 05 00 91 7f 01 08 eb a3 ff ff 54 02 00 00 14 0a 02 80 52 08 00 00 90 08 01 00 91 0b 79 6a f8 6b 02 00 f9 13 79 2a f8 f4 4f 4c a9 e0 03 09 aa f6 57 4b a9 f8 5f 4a a9 fa 67 49 a9 fd 7b 48 a9 ff 43 03 91 c0 03 5f d6 09 00 80 92 f7 ff ff 17 9f 04 04 f1 e2 00 00 54 88 3c 00 91 9f 00 00 f1 08 fd 44 d3 08 05 00 d1 e8 03 88 9a 11 00 00 14 9f 04 08 f1 c3 01 00 54 09 40 80 52 08 02 80 52 29 f9 7f d3 08 05 00 91 3f 01 04 eb a3 ff ff 54 08 00 00 14 e9 03 1f aa e4 03 17 aa e5 03 18 aa e2 03 1a aa e1 03 19 aa 78 ff ff 17 08 02 80 52 09 00 00 90 29 01 00 91 2a 79 68 f8 6a 02 00 f9 33 79 28 f8 09 00 80 92 d8 ff ff 17 e9 00 80 52 f4 ff ff 17 ※ data
pl4g_heap_free:
    60 04 00 b4 3f 40 40 f1 49 01 00 54 28 fc 3f 91 e2 03 1f aa e3 03 1f aa 01 cd 74 92 e8 1a 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 c0 03 5f d6 3f 04 04 f1 e2 00 00 54 28 3c 00 91 3f 00 00 f1 08 fd 44 d3 08 05 00 d1 e8 03 88 9a 0b 00 00 14 3f 04 08 f1 03 01 00 54 09 40 80 52 08 02 80 52 29 f9 7f d3 08 05 00 91 3f 01 01 eb a3 ff ff 54 02 00 00 14 08 02 80 52 09 00 00 90 29 01 00 91 2a 79 68 f8 0a 00 00 f9 20 79 28 f8 c0 03 5f d6 ※ data
pl4g_io_read_kept:
    fd 7b bc a9 f6 57 02 a9 f5 03 00 aa 40 40 00 91 f8 5f 01 a9 fd 03 00 91 f7 03 01 2a f4 4f 03 a9 f4 03 02 aa 58 40 00 91 00 00 00 94 40 02 00 b4 f6 03 00 aa f3 03 00 aa e0 03 15 aa d4 06 01 f8 e1 03 1f 2a e2 03 17 2a e3 03 16 aa e4 03 14 aa e5 03 1f aa 00 00 00 94 20 01 f8 b7 a8 0e 00 8b 29 00 80 52 16 6d 00 f9 14 8d 00 f9 09 ad 00 f9 29 00 00 14 60 01 80 92 27 00 00 14 1f 43 40 f1 c9 01 00 54 e8 01 82 52 e9 03 00 aa e0 03 13 aa 88 02 08 8b e2 03 1f aa e3 03 1f aa 01 cd 74 92 e8 1a 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 e0 03 09 aa 18 00 00 14 1f 07 04 f1 e2 00 00 54 88 7e 00 91 1f 03 00 f1 08 fd 44 d3 08 05 00 d1 e8 03 88 9a 0b 00 00 14 1f 07 08 f1 03 01 00 54 09 40 80 52 08 02 80 52 29 f9 7f d3 08 05 00 91 3f 01 18 eb a3 ff ff 54 02 00 00 14 08 02 80 52 09 00 00 90 29 01 00 91 2a 79 68 f8 6a 02 00 f9 33 79 28 f8 f4 4f 43 a9 f6 57 42 a9 f8 5f 41 a9 fd 7b c4 a8 c0 03 5f d6 ※ data
pl4g_heap_alloc:
    1f 40 40 f1 a9 01 00 54 08 fc 3f 91 e0 03 1f aa 62 00 80 52 01 cd 74 92 c8 1b 80 52 43 04 80 52 04 00 80 92 e5 03 1f aa 01 00 00 d4 1f 00 00 f1 e0 43 80 9a c0 03 5f d6 1f 04 04 f1 02 01 00 54 08 3c 00 91 1f 00 00 f1 08 fd 44 d3 08 15 9f 9a 09 ed 7c d3 08 05 00 d1 0c 00 00 14 1f 04 08 f1 03 01 00 54 09 40 80 52 08 02 80 52 29 f9 7f d3 08 05 00 91 3f 01 00 eb a3 ff ff 54 03 00 00 14 08 02 80 52 09 40 80 52 0a 00 00 90 4a 01 00 91 40 79 68 f8 80 00 00 b4 09 00 40 f9 49 79 28 f8 c0 03 5f d6 0a 00 00 90 0b 00 00 90 40 01 40 f9 a0 00 00 b4 68 01 40 f9 08 01 00 cb 1f 01 09 eb 82 01 00 54 e0 03 1f aa c8 1b 80 52 01 02 a0 52 62 00 80 52 43 04 80 52 04 00 80 92 e5 03 1f aa 01 00 00 d4 c0 00 f8 b7 08 00 44 91 68 01 00 f9 08 00 09 8b 48 01 00 f9 c0 03 5f d6 e0 03 1f aa c0 03 5f d6 ※ data
pl4g_io_taken:
    3f 1c 00 f1 88 03 00 54 08 60 05 91 09 79 61 f8 29 03 00 b4 29 f0 7d d3 0a 00 09 8b 1f 69 29 f8 48 4d 40 f9 40 6d 40 f9 49 8d 40 f9 5f 6d 00 f9 5f 8d 00 f9 48 00 f8 b7 c0 03 5f d6 00 40 00 f1 c0 ff ff 54 28 41 00 91 1f 41 40 f1 89 01 00 54 e8 01 82 52 e2 03 1f aa e3 03 1f aa 28 01 08 8b e4 03 1f aa e5 03 1f aa 01 cd 74 92 e8 1a 80 52 01 00 00 d4 e0 03 1f aa c0 03 5f d6 1f 05 04 f1 e2 00 00 54 29 7d 00 91 1f 01 00 f1 29 fd 44 d3 29 05 00 d1 e9 03 89 9a 0b 00 00 14 1f 05 08 f1 03 01 00 54 0a 40 80 52 09 02 80 52 4a f9 7f d3 29 05 00 91 5f 01 08 eb a3 ff ff 54 02 00 00 14 09 02 80 52 0a 00 00 90 4a 01 00 91 4b 79 69 f8 0b 00 00 f9 40 79 29 f8 e0 03 1f aa c0 03 5f d6 ※ data
pl4g_io_wait:
    3f 1c 00 f1 69 00 00 54 a0 02 80 92 c0 03 5f d6 fd 7b bd a9 f5 0b 00 f9 15 60 01 91 fd 03 00 91 f4 4f 02 a9 f3 03 01 aa f4 03 00 aa a8 7a 73 f8 1f 05 00 f1 a1 01 00 54 80 06 40 f9 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 60 01 f8 b7 e0 03 14 aa 0d 00 00 94 f2 ff ff 17 1f 09 00 f1 81 00 00 54 88 0e 13 8b 00 4d 40 f9 02 00 00 14 a0 02 80 92 bf 7a 33 f8 f4 4f 42 a9 f5 0b 40 f9 fd 7b c3 a8 c0 03 5f d6 08 ac 43 a9 e9 03 00 aa 0a 01 40 b9 6b fd df 88 5f 01 0b 6b c0 07 00 54 28 25 40 f9 2c 61 02 91 2d 61 01 91 2f 61 03 91 30 61 05 91 31 61 04 91 0e 01 40 b9 52 00 80 52 06 00 00 90 c6 00 00 91 0e 00 00 14 08 fd 3f 91 e2 03 1f aa e3 03 1f aa 01 cd 74 92 e8 1a 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 ff 79 27 f8 3f 7a 27 f8 4a 05 00 11 5f 01 0b 6b a0 04 00 54 28 29 40 f9 40 01 0e 0a 08 51 20 8b 07 01 40 f9 ff 1c 00 f1 08 ff ff 54 e0 79 67 f8 08 09 80 b9 b2 79 27 f8 88 79 27 f8 60 fe ff b4 08 7a 67 f8 28 fe ff b5 28 7a 67 f8 1f 41 40 f1 88 fc ff 54 1f 05 04 f1 e2 00 00 54 01 3d 00 91 1f 01 00 f1 21 fc 44 d3 21 04 00 d1 e1 03 81 9a 09 00 00 14 01 02 80 52 1f 05 08 f1 c3 00 00 54 02 40 80 52 42 f8 7f d3 21 04 00 91 5f 00 08 eb a3 ff ff 54 c8 78 61 f8 08 00 00 f9 c0 78 21 f8 d8 ff ff 17 ea 03 0b 2a 28 1d 40 f9 0a fd 9f 88 c0 03 5f d6 ※ data
pl4g_io_ready:
    3f 1c 00 f1 69 00 00 54 20 00 80 52 c0 03 5f d6 fd 7b be a9 f4 4f 01 a9 13 60 01 91 fd 03 00 91 68 7a 61 f8 1f 05 00 f1 41 01 00 54 08 00 40 f9 1f 05 00 f1 21 01 00 54 f4 03 01 aa ab ff ff 97 68 7a 74 f8 1f 05 00 f1 e0 07 9f 1a 04 00 00 14 20 00 80 52 02 00 00 14 e0 03 1f aa f4 4f 41 a9 fd 7b c2 a8 c0 03 5f d6 ※ data
pl4g_io_step:
    08 00 40 f9 1f 05 00 f1 81 04 00 54 00 0d 08 4e 01 80 c5 3c 02 80 c6 3c 08 a8 47 a9 09 ac 48 a9 42 8c e0 6e 20 8c e0 6e 1f 05 00 f1 e8 17 9f 1a 3f 05 00 f1 00 18 82 4e e9 17 9f 1a 5f 05 00 f1 29 15 89 1a 7f 05 00 f1 00 b8 b1 4e 0a 00 26 1e 08 01 0a 4b 08 01 09 0b e9 17 9f 1a 08 01 09 2a c8 01 00 34 09 04 40 f9 ea 03 00 aa 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e0 03 09 aa e4 03 1f aa e5 03 1f aa 01 00 00 d4 60 00 f8 b7 e0 03 0a aa 7b ff ff 17 c0 03 5f d6 ※ data
pl4g_io_open:
    7f fc 3f f1 69 00 00 54 60 04 80 92 c0 03 5f d6 fd 0f 1f f8 ff 07 40 d1 03 01 00 b4 e8 03 00 91 e9 03 03 aa 4a 14 40 38 6a 02 00 34 29 05 00 f1 0a 15 00 38 81 ff ff 54 e8 03 00 91 1f 08 00 71 1f 69 23 38 88 01 00 54 08 00 00 90 08 01 00 91 c3 36 80 52 02 59 60 f8 20 7c 40 93 e1 03 00 91 08 07 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 02 00 00 14 a0 02 80 92 ff 07 40 91 fd 07 41 f8 c0 03 5f d6 ※ data
pl4g_io_close:
    ff c3 00 d1 08 00 40 f9 f4 4f 02 a9 f4 03 01 2a fd 7b 01 a9 fd 43 00 91 1f 05 00 f1 01 05 00 54 00 0d 08 4e f3 03 00 aa e0 03 80 3d 60 82 c5 3c 61 82 c6 3c e2 03 c0 3d 68 aa 47 a9 21 8c e2 6e 00 8c e2 6e 69 ae 48 a9 1f 05 00 f1 00 18 81 4e e8 17 9f 1a 3f 05 00 f1 e9 17 9f 1a 5f 05 00 f1 29 15 89 1a 7f 05 00 f1 00 b8 b1 4e 0a 00 26 1e 08 01 0a 4b 08 01 09 0b e9 17 9f 1a 08 01 09 2a e8 01 00 34 60 06 40 f9 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 c0 00 f8 b7 e0 03 13 aa 2d ff ff 97 68 02 40 f9 1f 05 00 f1 a0 fb ff 54 80 7e 40 93 28 07 80 52 e1 03 1f aa e2 03 1f aa e3 03 1f aa e4 03 1f aa e5 03 1f aa 01 00 00 d4 f4 4f 42 a9 fd 7b 41 a9 ff c3 00 91 c0 03 5f d6 ※ data
pl4g_io_drain:
    ff c3 00 d1 08 00 40 f9 f3 13 00 f9 f3 03 00 aa fd 7b 01 a9 fd 43 00 91 1f 05 00 f1 e1 04 00 54 00 0d 08 4e e0 03 80 3d 60 82 c5 3c 61 82 c6 3c e2 03 c0 3d 68 aa 47 a9 21 8c e2 6e 00 8c e2 6e 69 ae 48 a9 1f 05 00 f1 00 18 81 4e e8 17 9f 1a 3f 05 00 f1 e9 17 9f 1a 5f 05 00 f1 29 15 89 1a 7f 05 00 f1 00 b8 b1 4e 0a 00 26 1e 08 01 0a 4b 08 01 09 0b e9 17 9f 1a 08 01 09 2a e8 01 00 34 60 06 40 f9 48 35 80 52 01 01 80 52 22 00 80 52 23 00 80 52 e4 03 1f aa e5 03 1f aa 01 00 00 d4 c0 00 f8 b7 e0 03 13 aa f3 fe ff 97 68 02 40 f9 1f 05 00 f1 a0 fb ff 54 00 e4 00 6f fd 7b 41 a9 60 82 85 3c 60 82 86 3c 60 82 87 3c 60 82 88 3c f3 13 40 f9 ff c3 00 91 c0 03 5f d6 ※ data

section .pl4grt.rodata.cst16
.Lpl4grt.1:
                             ※ align 16
    00 00 00 00 00 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ff ff ff ff ff ff ff ff ※ data

section .pl4grt.rodata
.Lpl4grt.2:
pl4g_io_shape:
                             ※ align 8
    98 01 00 00 00 00 00 00 08 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 10 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 18 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 20 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 28 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 30 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 38 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 40 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 48 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 50 00 00 00 00 00 00 00 08 00 00 00 00 00 00 00 58 00 00 00 00 00 00 00 40 00 00 00 00 00 00 00 98 00 00 00 00 00 00 00 40 00 00 00 00 00 00 00 d8 00 00 00 00 00 00 00 40 00 00 00 00 00 00 00 18 01 00 00 00 00 00 00 40 00 00 00 00 00 00 00 58 01 00 00 00 00 00 00 40 00 00 00 00 00 00 00 16 00 3f 00 17 00 40 00 02 00 42 00 00 00 00 00 00 00 08 00 00 00 00 00 41 02 08 00 00 00 00 00 42 00 08 00 00 00 00 00 ※ data

section .pl4grt.bss writable
.Lpl4grt.3:
                             ※ align 8
    00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 ※ data
