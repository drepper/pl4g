/* Loads the shared object host-symbol-missing.pl4g is built into and calls
   what it offers, which calls a function nothing defines: the program stops
   there with status 92 and never comes back here.  */
#include <dlfcn.h>

int main(int argc, char *argv[])
{
  if (argc < 2)
    return 1;
  void *h = dlopen(argv[1], RTLD_NOW);
  if (h == 0)
    return 2;
  int (*run)(void) = (int (*)(void)) dlsym(h, "run");
  if (run == 0)
    return 3;
  run();
  return 4;
}
