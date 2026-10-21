/* PL4G's interpreter, for a program that loads it as a shared object.

   libpl4g-embed.so offers this one function with C's linkage, so that a
   program -- in C or in anything that calls C -- can find it with dlsym after
   dlopen and needs none of the compiler's headers.  Everything else in the
   object is hidden.  A program wanting more than this links with the library
   and uses pl4g::embed::Interpreter (embed/embed.hh), which this wraps.

   What the PL4G program calls with @[host("SYMBOL")] is looked up with
   dlsym(RTLD_DEFAULT, SYMBOL): the program loading this object defines it,
   and has to have been linked with -rdynamic for its symbols to be found.  */
#ifndef PL4G_EMBED_H
#define PL4G_EMBED_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** The type of pl4g_embed_run_source, for the pointer dlsym answers.  */
typedef int (*pl4g_embed_run_source_t)(const char *text, size_t length, const char *name);

/** Compile the PL4G program in the LENGTH bytes at TEXT and run it.

    NAME is what its diagnostics and the messages of its faults call the
    file.  The program uses descriptors 0, 1 and 2 as its devices.  Answers
    the status it exited with; 1 where it did not compile, the diagnostics
    having gone to standard error; 128 plus the number of a signal it would
    have died of; and -1 where the interpreter itself failed.  */
int pl4g_embed_run_source(const char *text, size_t length, const char *name);

#ifdef __cplusplus
}
#endif

#endif /* PL4G_EMBED_H */
