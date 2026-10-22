/* PL4G's interpreter, for a program that loads it as a shared object.

   libpl4g-embed.so offers these four functions with C's linkage, so that a
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
   at a time.

   What the PL4G program calls with @[host("SYMBOL")] is looked up with
   dlsym(RTLD_DEFAULT, SYMBOL): the program loading this object defines it,
   and has to have been linked with -rdynamic for its symbols to be found.  */
#ifndef PL4G_EMBED_H
#define PL4G_EMBED_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** A state of the interpreter, whose insides are the library's.  */
typedef struct pl4g_embed_state pl4g_embed_state;

/** The types of the four functions, for the pointers dlsym answers.  */
typedef pl4g_embed_state *(*pl4g_embed_state_new_t)(void);
typedef void (*pl4g_embed_state_free_t)(pl4g_embed_state *state);
typedef int (*pl4g_embed_run_source_t)(pl4g_embed_state *state, const char *text, size_t length,
                                       const char *name);
typedef int (*pl4g_embed_define_t)(pl4g_embed_state *state, const char *symbol, void (*function)(void));

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
    void (*)(void) for the call and has to take and answer, with the system's
    convention, what the declaration says.  A later one for the same SYMBOL
    takes the place of the earlier.  Answers 0, and -1 where STATE, SYMBOL or
    FUNCTION is NULL or there is no memory to keep it.  */
int pl4g_embed_define(pl4g_embed_state *state, const char *symbol, void (*function)(void));

/** Compile the PL4G program in the LENGTH bytes at TEXT and run it in STATE.

    NAME is what its diagnostics and the messages of its faults call the
    file.  The program uses descriptors 0, 1 and 2 as its devices.  Answers
    the status it exited with; 1 where it did not compile, the diagnostics
    having gone to standard error; 128 plus the number of a signal it would
    have died of; and -1 where STATE is NULL or the interpreter itself
    failed.  */
int pl4g_embed_run_source(pl4g_embed_state *state, const char *text, size_t length, const char *name);

#ifdef __cplusplus
}
#endif

#endif /* PL4G_EMBED_H */
