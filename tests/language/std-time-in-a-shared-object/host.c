/* The program the shared object std-time-in-a-shared-object.pl4g is built into
   is loaded by.  What the object's `now` answers lies between two readings of
   the same clock made here, and its `step` is the resolution the C library
   reports.  It exits with 0 where both hold, and with a number saying what went
   wrong otherwise.  */
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>
#include <time.h>

static int64_t nanoseconds(const struct timespec *t)
{
  return (int64_t) t->tv_sec * 1000000000 + t->tv_nsec;
}

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == NULL)
    return 2;
  int64_t (*now)(void) = (int64_t (*)(void)) dlsym(h, "now");
  int64_t (*step)(void) = (int64_t (*)(void)) dlsym(h, "step");
  if (now == NULL || step == NULL)
    return 3;
  struct timespec before, after, res;
  clock_gettime(CLOCK_MONOTONIC, &before);
  int64_t got = now();
  clock_gettime(CLOCK_MONOTONIC, &after);
  if (got < nanoseconds(&before) || got > nanoseconds(&after))
    return 4;
  clock_getres(CLOCK_MONOTONIC, &res);
  if (step() != nanoseconds(&res))
    return 5;
  dlclose(h);
  return 0;
}
