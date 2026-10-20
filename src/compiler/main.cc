// The compiler's command line: `pl4g [COMMAND] [OPTION]... SOURCE.pl4g...`,
// what pypl4g's is.  Everything is the library's; this hands it the words and
// the descriptors the process was started with.
#include <string>
#include <vector>

#include "driver/driver.hh"


int main(int argc, char* argv[])
{
  std::vector<std::string> words(argv + 1, argv + argc);
  return pl4g::driver::run_command_line(words, pl4g::driver::Descriptors{ 0, 1, 2 });
}
