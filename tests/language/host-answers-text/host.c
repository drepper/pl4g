/* The program the shared object host-answers-text.pl4g is built into is
   loaded by.  It defines the host functions that object calls, each answering
   text as a pointer and a length, and keeps every byte it answers: the program
   copies them before the call returns to it.  It is linked with -rdynamic so
   that dlsym finds them.  It exits with 0 where the object saw every answer as
   it was sent, and with a number saying which it did not.  */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct text { const char *text; uint64_t length; };

struct text greeting(void) { return (struct text) { "hello, world", 12 }; }

/* A nul inside, and a character of three bytes after it.  */
struct text with_nul(void) { return (struct text) { "a\0b\xe2\x82\xac", 6 }; }

struct text empty(void) { return (struct text) { "", 0 }; }

/* No text at all: the length beside it is not looked at.  */
struct text nothing(void) { return (struct text) { NULL, 7 }; }

/* The arguments arrive as C's text, and the answer is in a buffer the host
   keeps until the next call.  */
struct text tool(const char *name, const char *arguments)
{
  static char kept[256];
  int n = snprintf(kept, sizeof kept, "%s(%s)", name, arguments);
  return (struct text) { kept, (uint64_t) n };
}

/* As many bytes as asked for, out of one buffer the host keeps.  */
struct text big(uint64_t n)
{
  static char *kept;
  static uint64_t size;
  if (size < n) {
    free(kept);
    kept = malloc(n);
    if (kept == NULL)
      return (struct text) { NULL, 0 };
    memset(kept, 'x', n);
    size = n;
  }
  return (struct text) { kept, n };
}

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 100;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == NULL)
    return 101;
  int64_t (*run)(void) = (int64_t (*)(void)) dlsym(h, "run");
  if (run == NULL)
    return 102;
  /* Twice: the second time each function is found where the first left it.  */
  int64_t first = run();
  if (first != 0)
    return (int) first;
  int64_t second = run();
  if (second != 0)
    return 10 + (int) second;
  dlclose(h);
  return 0;
}
