/* PL4G's interpreter, for a program that loads it as a shared object.

   libpl4g-embed.so offers these five functions with C's linkage, so that a
   program -- in C or in anything that calls C -- can find them with dlsym
   after dlopen and needs none of the compiler's headers.  Everything else in
   the object is hidden.  A program wanting more than this links with the
   library and uses pl4g::embed::Interpreter (embed/embed.hh), which this wraps.

   Everything a run changes that outlives it is in a state: the heap the
   program takes from, the seed its tables are worked out from, and what the
   runtime took from the system.  A state is made by pl4g_embed_state_new and
   given back, with all of that, by pl4g_embed_state_free; what is in one is
   nobody's business but the library's.  Several may exist at once, each run
   in one of them seeing none of the others; one state is run in by one thread
   at a time, and stopped from any (pl4g_embed_stop).

   What the PL4G program calls with @[host("SYMBOL")] is looked up with
   dlsym(RTLD_DEFAULT, SYMBOL): the program loading this object defines it,
   and has to have been linked with -rdynamic for its symbols to be found.

   What crosses is what C has: integers, floating-point numbers, bool, a
   character as uint32_t, a reference as the address of what it names -- a
   record marked @[abi] among them, which is also answered by value as C
   answers the struct -- and text.  A str is handed to the host as a
   nul-terminated copy, made for the call and given back after it.  A host
   function declared to answer str answers

       struct { const char *text; uint64_t length; }

   by value, in two registers: the host keeps the bytes, which the program
   copies into its own heap before the call returns to it, so they need only
   last until then and nothing of the host's is freed by PL4G.  A nul among
   the LENGTH bytes is a character like any other; a NULL text is the empty
   string, the length then not looked at; bytes that are not UTF-8 stop the
   program with status 94.  */
#ifndef PL4G_EMBED_H
#define PL4G_EMBED_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** A state of the interpreter, whose insides are the library's.  */
typedef struct pl4g_embed_state pl4g_embed_state;

/** The types of the five functions, for the pointers dlsym answers.  */
typedef pl4g_embed_state *(*pl4g_embed_state_new_t)(void);
typedef void (*pl4g_embed_state_free_t)(pl4g_embed_state *state);
typedef int (*pl4g_embed_run_source_t)(pl4g_embed_state *state, const char *text, size_t length,
                                       const char *name, const char *const *args, size_t count);
typedef int (*pl4g_embed_define_t)(pl4g_embed_state *state, const char *symbol, void (*function)(void),
                                   void *context);
typedef void (*pl4g_embed_stop_t)(pl4g_embed_state *state);

/** A new state, as fresh as the first: nothing allocated, nothing seeded.
    Answers NULL where there is no memory for one.  */
pl4g_embed_state *pl4g_embed_state_new(void);

/** Give back STATE and everything its runs took and did not give back.
    STATE may be NULL, which does nothing.  */
void pl4g_embed_state_free(pl4g_embed_state *state);

/** Hand STATE's runs FUNCTION as what a program's @[host("SYMBOL")] names
    with SYMBOL.  What is handed over is asked before dlsym is, so a host can
    give a program a function it does not export -- one of internal linkage,
    or any function of a program not linked with -rdynamic -- or put one of
    its own where an exported symbol of that name is.  FUNCTION is cast to
    void (*)(void) for the call and has to answer, with the system's
    convention, what the declaration says, and to take it: where CONTEXT is
    not NULL, FUNCTION takes CONTEXT first, as a void *, and what the
    declaration says after it -- which is how one function serves several
    objects of the host's; where it is NULL, only what the declaration says.
    A later one for the same SYMBOL takes the place of the earlier.  Answers
    0, and -1 where STATE, SYMBOL or FUNCTION is NULL or there is no memory
    to keep it.  */
int pl4g_embed_define(pl4g_embed_state *state, const char *symbol, void (*function)(void), void *context);

/** Compile the PL4G program in the LENGTH bytes at TEXT and run it in STATE.

    NAME is what its diagnostics and the messages of its faults call the
    file.  The program is started with the COUNT nul-terminated strings at
    ARGS after its own name, which is what its startup function finds in
    `init.args`, as a compiled program finds the words it was named with;
    ARGS may be NULL where COUNT is 0, and the program then has its name
    alone.  The program uses descriptors 0, 1 and 2 as its devices.  Answers
    the status it exited with; 1 where it did not compile, the diagnostics
    having gone to standard error; 128 plus the number of a signal it would
    have died of; and -1 where STATE is NULL or the interpreter itself
    failed, or ARGS is NULL and COUNT is not 0.  */
int pl4g_embed_run_source(pl4g_embed_state *state, const char *text, size_t length, const char *name,
                          const char *const *args, size_t count);

/** Stop the program running in STATE, from any thread: it ends at its next
    jump back or call, or, where it is in a function of the host's, as that
    returns -- a host call in progress is not interrupted -- and
    pl4g_embed_run_source answers 93, as for any other stop.  A stop is for
    the run in progress: one made while nothing runs in STATE does nothing,
    every run starting with none asked for.  STATE may be NULL, which does
    nothing.  */
void pl4g_embed_stop(pl4g_embed_state *state);

#ifdef __cplusplus
}
#endif

#endif /* PL4G_EMBED_H */
