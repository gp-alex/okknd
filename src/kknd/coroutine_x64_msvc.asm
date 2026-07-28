; coroutine_x64_msvc.asm - Win64 stackful coroutine context switch for MSVC.
;
; MSVC (cl.exe) on x64 supports neither inline __asm nor __declspec(naked), so
; TSK_execute_async is provided here for that toolchain. clang/gcc (incl.
; clang-cl) use the inline-asm version in kknd.c instead.
;
; Build: ml64 /c /Fo coroutine_x64_msvc.obj coroutine_x64_msvc.asm
;        then add coroutine_x64_msvc.obj to the link.
;
; ABI: Win64 -- next in RCX; non-volatiles rbx,rbp,rsi,rdi,r12-r15,xmm6-xmm15.
; Coroutine layout: yield_to@0, context@8, stack@16.
; Must stay in lockstep with the frame synthesized by TSK_coroutine_create.

EXTERN g_coroutine_current:QWORD
EXTERN g_coroutine_current_stack:QWORD
EXTERN g_coroutine_list_head:QWORD
EXTERN g_coroutine_esp:QWORD
EXTERN g_coroutine_borrow_stack_top:QWORD
EXTERN g_coroutine_nesting_depth:DWORD

.code

; void TSK_execute_async(Coroutine *next);
TSK_execute_async PROC
    push    rbp
    push    rbx
    push    rsi
    push    rdi
    push    r12
    push    r13
    push    r14
    push    r15
    sub     rsp, 160                    ; xmm6-xmm15 save area (movdqu: no align req)
    movdqu  [rsp+0],   xmm6
    movdqu  [rsp+16],  xmm7
    movdqu  [rsp+32],  xmm8
    movdqu  [rsp+48],  xmm9
    movdqu  [rsp+64],  xmm10
    movdqu  [rsp+80],  xmm11
    movdqu  [rsp+96],  xmm12
    movdqu  [rsp+112], xmm13
    movdqu  [rsp+128], xmm14
    movdqu  [rsp+144], xmm15

    mov     rax, g_coroutine_current
    mov     [rax+16], rsp               ; current->stack = rsp
    mov     [rcx+0], rax                ; next->yield_to = current
    mov     g_coroutine_current, rcx    ; current = next
    mov     rsp, [rcx+16]               ; rsp = next->stack
    mov     g_coroutine_current_stack, rsp

    movdqu  xmm6,  [rsp+0]
    movdqu  xmm7,  [rsp+16]
    movdqu  xmm8,  [rsp+32]
    movdqu  xmm9,  [rsp+48]
    movdqu  xmm10, [rsp+64]
    movdqu  xmm11, [rsp+80]
    movdqu  xmm12, [rsp+96]
    movdqu  xmm13, [rsp+112]
    movdqu  xmm14, [rsp+128]
    movdqu  xmm15, [rsp+144]
    add     rsp, 160
    pop     r15
    pop     r14
    pop     r13
    pop     r12
    pop     rdi
    pop     rsi
    pop     rbx
    pop     rbp
    ret
TSK_execute_async ENDP

; void COROUTINE_STACK_BORROW_ASM(void);
; Only the outermost borrow (depth 0->1) relocates rsp onto the borrow stack.
COROUTINE_STACK_BORROW_ASM PROC
    mov     rax, g_coroutine_list_head
    cmp     rax, g_coroutine_current
    jz      brw_skip
    inc     g_coroutine_nesting_depth
    cmp     g_coroutine_nesting_depth, 1
    jnz     brw_skip
    pop     rcx                         ; return address
    mov     g_coroutine_esp, rsp        ; save caller's real rsp
    mov     rsp, g_coroutine_borrow_stack_top
    push    rcx                         ; relocate return addr
  brw_skip:
    ret
COROUTINE_STACK_BORROW_ASM ENDP

; void COROUTINE_STACK_RETURN_ASM(void);
COROUTINE_STACK_RETURN_ASM PROC
    mov     rax, g_coroutine_list_head
    cmp     rax, g_coroutine_current
    jz      ret_skip
    dec     g_coroutine_nesting_depth
    jnz     ret_skip
    pop     rcx
    mov     rsp, g_coroutine_esp        ; restore caller's real rsp
    push    rcx
  ret_skip:
    ret
COROUTINE_STACK_RETURN_ASM ENDP

END
