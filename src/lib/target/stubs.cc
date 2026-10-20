// TEMPORARY: a stand-in for the RISC-V back end until its port lands; this
// file goes when it does.
#include "target/target.hh"


namespace pl4g::target {

  std::unique_ptr<Target> make_riscv64(std::string_view) { return nullptr; }

} // namespace pl4g::target
