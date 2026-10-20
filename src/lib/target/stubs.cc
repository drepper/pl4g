// TEMPORARY: stand-ins for the three architectures' back ends until their
// ports land; each architecture's agent deletes its own and this file goes
// once all three are in.
#include "target/target.hh"


namespace pl4g::target {

  std::unique_ptr<Target> make_x86_64(std::string_view) { return nullptr; }
  std::unique_ptr<Target> make_riscv64(std::string_view) { return nullptr; }

} // namespace pl4g::target
