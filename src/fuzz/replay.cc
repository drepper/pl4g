// A driver for the fuzzing entry points without a fuzzer: every file named,
// and every file under every directory named, is handed to the entry point
// once.  This is what ctest runs over the corpus and the test programs, built
// with the sanitizers, and how a crash a fuzzer found is reproduced.
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <print>
#include <string>
#include <system_error>
#include <vector>


extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size);


namespace {

  // Hand the contents of `path` to the entry point.
  void replay(const std::filesystem::path& path)
  {
    std::ifstream in(path, std::ios::binary);
    std::vector<char> data{ std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>() };
    LLVMFuzzerTestOneInput(reinterpret_cast<const std::uint8_t*>(data.data()), data.size());
  }

} // anonymous namespace


int main(int argc, char* argv[])
{
  unsigned count = 0;
  for (int i = 1; i < argc; ++i) {
    std::filesystem::path path(argv[i]);
    std::error_code ec;
    if (std::filesystem::is_directory(path, ec)) {
      for (const auto& entry : std::filesystem::recursive_directory_iterator(path, ec))
        if (entry.is_regular_file()) {
          replay(entry.path());
          ++count;
        }
    } else if (std::filesystem::exists(path, ec)) {
      replay(path);
      ++count;
    }
  }
  std::println("{} inputs replayed", count);
}
