/* The program the shared object relro-in-a-shared-object.pl4g is built into is
   loaded by.  The object's constructor writes its `@[relro]` variables when it
   is loaded, and what it offers reads them: 3 × 20 + 2 is what it answers where
   the constructor ran.  It exits with 0 where that is what came back, and with
   a number saying what went wrong otherwise.  */
#include <dlfcn.h>
#include <stddef.h>
#include <stdint.h>

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == NULL)
    return 2;
  int64_t (*run)(int64_t) = (int64_t (*)(int64_t)) dlsym(h, "run");
  if (run == NULL)
    return 3;
  if (run(3) != 62)
    return 4;
  dlclose(h);
  return 0;
}
