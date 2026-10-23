/* The program the shared object constinit-in-a-shared-object.pl4g is built into
   is loaded by.  The object's initializers give its `constinit` variables their
   values when it is loaded, before its constructor, and what it offers reads
   them: 3 × 20 + 2, the constructor having read 20 too, is what it answers
   where both ran in that order.  It exits with 0 where that is what came back,
   and with a number saying what went wrong otherwise.  */
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
