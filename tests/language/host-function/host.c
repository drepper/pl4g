/* The program the shared object host-function.pl4g is built into is loaded
   by.  It defines what that object's host functions name, and is linked with
   -rdynamic so that dlsym finds them in it.  It exits with 0 where everything
   arrived as it was sent, and with a number saying what did not.  */
#include <dlfcn.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

struct pair { int64_t first; double second; };

static int64_t notes;

void note(int64_t what) { notes = notes * 10 + what; }

int64_t join_words(const char *a, const char *b)
{
  return strcmp(a, "host") == 0 && strcmp(b, "functions") == 0
         ? (int64_t) (strlen(a) + strlen(b)) : -1000;
}

int64_t many(int64_t a, int64_t b, int64_t c, int64_t d, int64_t e, int64_t f, double g, double h)
{
  return a + b + c + d + e + f + (int64_t) g + (h == 0.5 ? 0 : 1000);
}

bool scale(struct pair *p, double by)
{
  p->first = (int64_t) (p->first * by);
  p->second *= by;
  return p->second == 3.0;
}

void bump(int64_t *n) { ++*n; }

uint32_t next_char(uint32_t c) { return c + 1; }

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == NULL)
    return 2;
  if (notes != 1)
    return 3;
  int64_t (*run)(int64_t) = (int64_t (*)(int64_t)) dlsym(h, "run");
  if (run == NULL)
    return 4;
  /* Twice: the second call finds each function where the first left it.  */
  if (run(5) != 83 || run(5) != 83)
    return 5;
  dlclose(h);
  return notes == 14 ? 0 : 6;
}
